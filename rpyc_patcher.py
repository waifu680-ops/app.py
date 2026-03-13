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
    """Sadece eşleşen kelime/cümle öbeklerini değiştirir, veri tipini korur."""
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
    """AST ağacını zincirleri KIRMADAN tarayan motor."""
    if visited is None: visited = set()
    if obj is None or isinstance(obj, (int, float, bool, str, bytes)): return
    
    obj_id = id(obj)
    if obj_id in visited: return
    visited.add(obj_id)

    if isinstance(obj, (list, tuple)):
        for item in obj:
            patch_ast(item, translations, visited)
            
    elif isinstance(obj, dict):
        for k, v in obj.items():
            patch_ast(v, translations, visited)
            
    elif hasattr(obj, '__dict__'):
        class_name = type(obj).__name__
        
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
            if hasattr(obj, 'new'):
                obj.new = apply_translation(obj.new, translations)

        for k, v in obj.__dict__.items():
            patch_ast(v, translations, visited)

def process_rpyc_file(file_bytes, raw_translations):
    """Hem Slot 1'i hem Slot 2'yi yamalayan ve Orijinal Protokolü koruyan Nihai Motor."""
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
            if slot == 0: break
            chunks.append({"slot": slot, "start": start, "length": length})
            
        payloads = {}
        
        for c in chunks:
            chunk_data = file_bytes[c["start"] : c["start"] + c["length"]]
            
            if c["slot"] == 1:
                raw_pickle = zlib.decompress(chunk_data)
                
                # --- İŞTE O İPUCU: PROTOKOLÜ DİNAMİK OLARAK TESPİT ET ---
                orig_proto = 2
                # Pickle dosyaları \x80 byte'ı ile başlar ve hemen ardından protokol sürümü (2,3,4,5) gelir.
                if len(raw_pickle) >= 2 and raw_pickle[0] == 0x80:
                    orig_proto = raw_pickle[1]
                    
                ast_tree = renpycompat.pickle_loads(raw_pickle)
                patch_ast(ast_tree, clean_translations)
                
                # Oyunun kendi orijinal protokolüyle tekrar paketle!
                new_pickle = pickle.dumps(ast_tree, protocol=orig_proto) 
                payloads[1] = zlib.compress(new_pickle)
                
            elif c["slot"] == 2:
                # İkinci ipucu: Orijinal kaynak kod (Slot 2) metinlerini de Türkçeleştir!
                try:
                    raw_source = zlib.decompress(chunk_data).decode('utf-8')
                    for k, v in clean_translations.items():
                        raw_source = raw_source.replace(k, v)
                    payloads[2] = zlib.compress(raw_source.encode('utf-8'))
                except Exception:
                    payloads[2] = chunk_data
            else:
                payloads[c["slot"]] = chunk_data
                
        # Dosyayı orijinal şablonuyla geri birleştir
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
        # Eski V1 Formatı için Protokol Tespiti
        raw_pickle = zlib.decompress(file_bytes)
        
        orig_proto = 2
        if len(raw_pickle) >= 2 and raw_pickle[0] == 0x80:
            orig_proto = raw_pickle[1]
            
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        patch_ast(ast_tree, clean_translations)
        return zlib.compress(pickle.dumps(ast_tree, protocol=orig_proto))
