import struct
import zlib
import pickle
import sys
from decompiler import magic, renpycompat

# Ren'Py motorunun derinlikleri için sınırı artırıyoruz
sys.setrecursionlimit(50000)

def unescape_wp_string(s):
    """WordPress'ten gelen metinleri Python formatına uydurur."""
    if not isinstance(s, str): return s
    s = s.replace('\\n', '\n')
    s = s.replace('\\"', '"')
    s = s.replace('\\ ', ' ')
    s = s.replace('\\\\', '\\')
    return s.replace('\r', '')

def apply_translation(text, translations):
    """Sadece eşleşen kelime/cümle öbeklerini değiştirir, kodu bozmaz."""
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
    """AST ağacını zincirleri (tuple) KIRMADAN tarayan Hayalet Enjektör."""
    if visited is None: visited = set()
    if obj is None or isinstance(obj, (int, float, bool, str, bytes)): return
    
    obj_id = id(obj)
    if obj_id in visited: return
    visited.add(obj_id)

    # 1. Liste veya Zincir (Tuple) ise içine gir ama yapısını BOZMA
    if isinstance(obj, (list, tuple)):
        for item in obj:
            patch_ast(item, translations, visited)
            
    # 2. Sözlük ise değerlerin içine gir
    elif isinstance(obj, dict):
        for k, v in obj.items():
            patch_ast(v, translations, visited)
            
    # 3. Asıl Hedef: Oyun Objeleri
    elif hasattr(obj, '__dict__'):
        class_name = type(obj).__name__
        
        # Sadece diyalog ve menülere müdahale ediyoruz!
        if class_name == 'Say' and hasattr(obj, 'what'):
            obj.what = apply_translation(obj.what, translations)
            
        elif class_name == 'Menu' and hasattr(obj, 'items'):
            new_items = []
            for item in obj.items:
                label = apply_translation(item[0], translations)
                if len(item) >= 3:
                    new_items.append((label, item[1], item[2]))
                else:
                    new_items.append((label,) + item[1:])
            obj.items = new_items
            
        elif class_name == 'TranslateString':
            # Modern Ren'Py sürümlerinde çevirilerin tutulduğu yer
            if hasattr(obj, 'new'):
                obj.new = apply_translation(obj.new, translations)

        # Diğer özelliklerin içindeki alt objelere inmeye devam et
        for k, v in obj.__dict__.items():
            patch_ast(v, translations, visited)

def process_rpyc_file(file_bytes, raw_translations):
    """Dosya yapısını %100 koruyarak sadece AST'yi güncelleyen ana motor."""
    sorted_keys = sorted(raw_translations.keys(), key=len, reverse=True)
    clean_translations = {}
    for k in sorted_keys:
        clean_k = unescape_wp_string(k)
        if clean_k.strip():
            clean_translations[clean_k] = unescape_wp_string(raw_translations[k])

    if file_bytes.startswith(b"RENPY RPC2"):
        position = 10
        chunks = []
        
        # Orijinal haritayı çıkar
        while True:
            slot, start, length = struct.unpack("III", file_bytes[position:position+12])
            position += 12
            if slot == 0: break
            chunks.append({"slot": slot, "start": start, "length": length})
            
        slot1 = next((c for c in chunks if c["slot"] == 1), None)
        if not slot1: raise ValueError("Geçerli bir kod bölümü (Slot 1) bulunamadı.")
            
        # Makine dilini çıkar ve yama yap
        zlib_data = file_bytes[slot1["start"] : slot1["start"] + slot1["length"]]
        raw_pickle = zlib.decompress(zlib_data)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        
        patch_ast(ast_tree, clean_translations)
        
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        new_zlib = zlib.compress(new_pickle)
        
        # --- DOSYAYI %100 ORİJİNAL YAPIDA GERİ BİRLEŞTİR ---
        payloads = {}
        for c in chunks:
            if c["slot"] == 1:
                # Sadece Slot 1'i (bizim güncellediğimiz kısım) değiştir
                payloads[c["slot"]] = new_zlib
            else:
                # Slot 2 (Kaynak kod) dahil tüm diğer parçaları olduğu gibi kopyala
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
        # Eski V1 Formatı
        raw_pickle = zlib.decompress(file_bytes)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        patch_ast(ast_tree, clean_translations)
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        return zlib.compress(new_pickle)
