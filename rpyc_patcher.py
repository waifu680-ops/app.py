import struct
import zlib
import pickle
import sys
from decompiler import magic, renpycompat

sys.setrecursionlimit(50000)

def unescape_wp_string(s):
    """WordPress'ten gelen metinleri formatlar."""
    if not isinstance(s, str): return s
    s = s.replace('\\n', '\n')
    s = s.replace('\\"', '"')
    s = s.replace('\\ ', ' ')
    s = s.replace('\\\\', '\\')
    return s.replace('\r', '')

def apply_translation(text, translations):
    """Sadece eşleşen çevirileri güvenle uygular."""
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
    """Geliştiricinin ipucuna göre hedeflenmiş kusursuz AST Yamalayıcı."""
    if visited is None: visited = set()
    if obj is None or isinstance(obj, (int, float, bool, str, bytes)): return
    
    obj_id = id(obj)
    if obj_id in visited: return
    visited.add(obj_id)

    # Listelerdeki pasif metinleri doğrudan mutasyona uğrat
    if isinstance(obj, list):
        for i in range(len(obj)):
            if isinstance(obj[i], (str, bytes)):
                obj[i] = apply_translation(obj[i], translations)
            else:
                patch_ast(obj[i], translations, visited)
                
    elif isinstance(obj, tuple):
        for item in obj:
            patch_ast(item, translations, visited)
            
    # Sözlük yapısındaki verileri iterasyon çökmesini engelleyerek yama
    elif isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, (str, bytes)):
                obj[k] = apply_translation(v, translations)
            else:
                patch_ast(v, translations, visited)
                
    elif hasattr(obj, '__dict__'):
        class_name = type(obj).__name__
        
        # --- 1. GİZLİ SINIFLAR ---
        if class_name in ('Say', 'TranslateSay') and hasattr(obj, 'what'):
            obj.what = apply_translation(obj.what, translations)
            
        elif class_name in ('Menu', 'TranslateMenu') and hasattr(obj, 'items'):
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
                
        # --- 2. GÜVENLİ DERİN TARAMA ---
        FORBIDDEN_KEYS = {'old', 'language', 'identifier', 'filename', 'name', 'label'}
        
        # obj.__dict__ üzerinde iterasyon yaparken list() kullanarak Runtime hatalarını engelle
        for k, v in list(obj.__dict__.items()):
            if k in FORBIDDEN_KEYS:
                continue
                
            if class_name in ('Say', 'TranslateSay') and k == 'what': continue
            if class_name in ('Menu', 'TranslateMenu') and k == 'items': continue
            if class_name == 'TranslateString' and k == 'new': continue
            
            if isinstance(v, (str, bytes)):
                text_len = len(v) if isinstance(v, str) else len(v.decode('utf-8', 'ignore'))
                if text_len >= 2:
                    setattr(obj, k, apply_translation(v, translations))
            else:
                patch_ast(v, translations, visited)

def process_rpyc_file(file_bytes, raw_translations):
    """Tüm parçaları %100 orijinal format ve protokolünde birleştiren Ana Motor."""
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
                
                orig_proto = 2
                if len(raw_pickle) >= 2 and raw_pickle[0] == 0x80:
                    orig_proto = raw_pickle[1]
                    
                ast_tree = renpycompat.pickle_loads(raw_pickle)
                patch_ast(ast_tree, clean_translations)
                
                new_pickle = pickle.dumps(ast_tree, protocol=orig_proto) 
                payloads[1] = zlib.compress(new_pickle)
                
            elif c["slot"] == 2:
                try:
                    raw_source = zlib.decompress(chunk_data).decode('utf-8')
                    for k, v in clean_translations.items():
                        raw_source = raw_source.replace(k, v)
                    payloads[2] = zlib.compress(raw_source.encode('utf-8'))
                except Exception:
                    payloads[2] = chunk_data
            else:
                payloads[c["slot"]] = chunk_data
                
        new_dir = bytearray()
        current_offset = 10 + (len(chunks) + 1) * 12
        
        for c in chunks:
            data = payloads[c["slot"]]
            new_dir.extend(struct.pack("III", c["slot"], current_offset, len(data)))
            current_offset += len(data)
            
        new_dir.extend(struct.pack("III", 0, 0, 0)) 
        
        new_file = bytearray(b"RENPY RPC2")
        new_file.extend(new_dir)
        for c in chunks:
            new_file.extend(payloads[c["slot"]])
            
        return bytes(new_file)
        
    else:
        raw_pickle = zlib.decompress(file_bytes)
        orig_proto = 2
        if len(raw_pickle) >= 2 and raw_pickle[0] == 0x80:
            orig_proto = raw_pickle[1]
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        patch_ast(ast_tree, clean_translations)
        return zlib.compress(pickle.dumps(ast_tree, protocol=orig_proto))
