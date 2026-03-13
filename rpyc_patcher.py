import struct
import zlib
import pickle
import sys
from decompiler import magic, renpycompat

# Ren'Py motorunun binlerce satırlık derinliğine inebilmek için sınırı artırıyoruz
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
    """Sadece verilen metin içinde geçen alt-cümleleri güvenli bir şekilde değiştirir."""
    if not isinstance(text, (str, bytes)): return text
    is_bytes = isinstance(text, bytes)
    text_str = text.decode('utf-8', 'ignore') if is_bytes else text
    
    original_text_str = text_str
    for k, v in translations.items():
        if k in text_str:
            text_str = text_str.replace(k, v)
            
    if text_str != original_text_str:
        return text_str.encode('utf-8') if is_bytes else text_str
    return text

def patch_ast(obj, translations, visited=None):
    """AST ağacını güvenli bir şekilde tarar. YALNIZCA diyaloglara müdahale eder, kodu asla bozmaz."""
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
        # Tuple içindeki veriler değiştiyse yeni bir tuple inşa et
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
        
        # SADECE VE SADECE DİYALOG VE METİN SINIFLARINA DOKUN! (Oyunun çökmemesi için en hayati kısım)
        
        # 1. Normal Diyaloglar
        if class_name == 'Say' and hasattr(obj, 'what'):
            obj.what = apply_translation(obj.what, translations)
            
        # 2. Seçim Menüleri
        elif class_name == 'Menu' and hasattr(obj, 'items'):
            new_items = []
            for item in obj.items:
                label = apply_translation(item[0], translations)
                if len(item) >= 3:
                    new_items.append((label, item[1], item[2]))
                else:
                    new_items.append((label,) + item[1:])
            obj.items = new_items
            
        # 3. Çeviri Belleği (Modern Ren'Py oyunlarında yazıların tutulduğu asıl yer)
        elif class_name == 'TranslateString':
            if hasattr(obj, 'new'):
                obj.new = apply_translation(obj.new, translations)
            if hasattr(obj, 'old'):
                obj.old = apply_translation(obj.old, translations)

        # Alt düğümlere inmeye devam et ama onlara rastgele string işlemi UYGULAMA!
        for k, v in obj.__dict__.items():
            new_v = patch_ast(v, translations, visited)
            if new_v is not v:
                obj.__dict__[k] = new_v
                
        return obj
    return obj

def process_rpyc_file(file_bytes, raw_translations):
    """Orijinal RPYC dosyasını açar, içini güvenli bir şekilde Türkçeleştirir ve paketler."""
    
    # Kelime çakışmalarını önlemek için en uzun cümlen en kısaya göre sırala
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
        
        # Güvenli Yamalama İşlemi
        patch_ast(ast_tree, clean_translations)
        
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        new_zlib = zlib.compress(new_pickle)
        
        # --- KUSURSUZ YENİDEN İNŞA (REPACKING) ---
        payloads = {}
        for c in chunks:
            if c["slot"] == 1:
                payloads[1] = new_zlib
            elif c["slot"] == 2:
                # Slot 2'yi SİLMİYORUZ, sadece içini (İngilizce kodu) güvenli bir şekilde SIFIRLIYORUZ!
                payloads[2] = zlib.compress(b"")
            else:
                payloads[c["slot"]] = file_bytes[c["start"] : c["start"] + c["length"]]
                
        new_dir = bytearray()
        current_offset = 10 + (len(chunks) + 1) * 12
        
        for c in chunks:
            data = payloads[c["slot"]]
            new_dir.extend(struct.pack("III", c["slot"], current_offset, len(data)))
            current_offset += len(data)
            
        new_dir.extend(struct.pack("III", 0, 0, 0)) # Harita Sonu
        
        new_file = bytearray(b"RENPY RPC2")
        new_file.extend(new_dir)
        for c in chunks:
            new_file.extend(payloads[c["slot"]])
            
        return bytes(new_file)
        
    else:
        # Eski V1 Formatındaki Oyunlar İçin
        raw_pickle = zlib.decompress(file_bytes)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        patch_ast(ast_tree, clean_translations)
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        return zlib.compress(new_pickle)
