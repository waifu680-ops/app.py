import struct
import zlib
import pickle
import sys
from decompiler import magic, renpycompat

# Sonsuz döngü kalkanı
sys.setrecursionlimit(50000)

def unescape_wp_string(s):
    """WordPress'ten gelen metinleri Python makine dili formatına kusursuz uydurur."""
    if not isinstance(s, str): return s
    s = s.replace('\\n', '\n')
    s = s.replace('\\"', '"')
    s = s.replace('\\ ', ' ')
    s = s.replace('\\\\', '\\')
    return s.replace('\r', '')

def apply_translation(text, translations):
    """Diyaloglar içinde geçen alt-cümleleri tespit edip çevirir."""
    if not isinstance(text, (str, bytes)): return text
    is_bytes = isinstance(text, bytes)
    text_str = text.decode('utf-8') if is_bytes else text
    
    original_text_str = text_str
    for k, v in translations.items():
        if k in text_str:
            text_str = text_str.replace(k, v)
            
    if text_str != original_text_str:
        return text_str.encode('utf-8') if is_bytes else text_str
    return text

def patch_ast(obj, translations, visited=None):
    """AST ağacındaki ÇELİK KASALARI (Tuple) kırıp her bir metne nüfuz eder."""
    if visited is None: visited = set()
    if obj is None or isinstance(obj, (int, float, bool, str, bytes)): return obj
    
    obj_id = id(obj)
    if obj_id in visited: return obj
    visited.add(obj_id)

    if isinstance(obj, list):
        for i in range(len(obj)):
            obj[i] = patch_ast(obj[i], translations, visited)
        return obj
    elif isinstance(obj, tuple):
        # Tuple Kırıcı: Değiştirilemez veriyi yeniden inşa et
        new_tuple = []
        changed = False
        for item in obj:
            new_item = patch_ast(item, translations, visited)
            new_tuple.append(new_item)
            if new_item is not item: changed = True
        return tuple(new_tuple) if changed else obj
    elif isinstance(obj, dict):
        for k, v in list(obj.items()):
            obj[k] = patch_ast(v, translations, visited)
        return obj
    elif hasattr(obj, '__dict__'):
        class_name = type(obj).__name__
        
        # 1. Diyalog ve Menüleri Doğrudan Değiştir
        if class_name == 'Say' and hasattr(obj, 'what'):
            obj.what = apply_translation(obj.what, translations)
        
        elif class_name == 'Menu' and hasattr(obj, 'items'):
            new_items = []
            for item in obj.items:
                label = item[0]
                new_label = apply_translation(label, translations)
                if len(item) >= 3:
                    new_items.append((new_label, item[1], item[2]))
                else:
                    new_items.append((new_label,) + item[1:])
            obj.items = new_items
            
        elif class_name == 'TranslateString' and hasattr(obj, 'new'):
            obj.new = apply_translation(obj.new, translations)

        # 2. Geri Kalan Tüm Gizli Kod Değişkenlerine Sız
        for k, v in list(obj.__dict__.items()):
            if class_name == 'Say' and k == 'what': continue
            if class_name == 'Menu' and k == 'items': continue
            if class_name == 'TranslateString' and k == 'new': continue
            
            # Tam eşleşen bir düz metin veya Python değişkeni (PyExpr) varsa çevir
            if isinstance(v, str):
                clean_v = v.replace('\r', '')
                if clean_v in translations:
                    obj.__dict__[k] = type(v)(translations[clean_v])
            elif isinstance(v, bytes):
                v_str = v.decode('utf-8', 'ignore').replace('\r', '')
                if v_str in translations:
                    obj.__dict__[k] = type(v)(translations[v_str].encode('utf-8'))
            else:
                new_v = patch_ast(v, translations, visited)
                if new_v is not v:
                    obj.__dict__[k] = new_v
        return obj
    return obj

def process_rpyc_file(file_bytes, raw_translations):
    """Orijinal RPYC dosyasını açar, SLOT 2'yi tamamen siler ve %100 saf Türkçe paketler."""
    sorted_keys = sorted(raw_translations.keys(), key=len, reverse=True)
    clean_translations = {}
    for k in sorted_keys:
        clean_k = unescape_wp_string(k)
        if clean_k.strip():
            clean_translations[clean_k] = unescape_wp_string(raw_translations[k])

    if file_bytes.startswith(b"RENPY RPC2"):
        position = 10
        chunks = []
        
        while True:
            slot, start, length = struct.unpack("III", file_bytes[position:position+12])
            position += 12
            if slot == 0:
                break
            chunks.append({"slot": slot, "start": start, "length": length})
            
        slot1 = next((c for c in chunks if c["slot"] == 1), None)
        if not slot1:
            raise ValueError("Geçerli bir kod bölümü (Slot 1) bulunamadı.")
            
        zlib_data = file_bytes[slot1["start"] : slot1["start"] + slot1["length"]]
        raw_pickle = zlib.decompress(zlib_data)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        
        # MAKİNE DİLİNE ÇEVİRİLERİ ENJEKTE ET!
        patch_ast(ast_tree, clean_translations)
        
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        new_zlib = zlib.compress(new_pickle)
        
        # --- EN BÜYÜK SİLAH: SLOT 2'Yİ (ESKİ KAYNAK KODU) SİL VE YOK ET ---
        new_dir = bytearray()
        valid_chunks = [c for c in chunks if c["slot"] != 2] # 2 Numaralı Slot'u dosyadan atıyoruz
        
        current_offset = 10 + (len(valid_chunks) + 1) * 12
        data_blocks = []
        
        for chunk in valid_chunks:
            if chunk["slot"] == 1:
                data = new_zlib
            else:
                data = file_bytes[chunk["start"] : chunk["start"] + chunk["length"]]
            
            new_dir.extend(struct.pack("III", chunk["slot"], current_offset, len(data)))
            data_blocks.append(data)
            current_offset += len(data)
            
        new_dir.extend(struct.pack("III", 0, 0, 0)) # Directory Sonu
        
        new_file = bytearray(b"RENPY RPC2")
        new_file.extend(new_dir)
        for data in data_blocks:
            new_file.extend(data)
            
        return bytes(new_file)
        
    else:
        # Eski V1 Formatındaki Oyunlar İçin
        raw_pickle = zlib.decompress(file_bytes)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        patch_ast(ast_tree, clean_translations)
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        return zlib.compress(new_pickle)
