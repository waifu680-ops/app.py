import struct
import zlib
import pickle
import sys
from decompiler import magic, renpycompat

# Ren'Py oyun dosyaları binlerce satır (düğüm) uzunluğunda olabilir.
# Python'un varsayılan 1000 olan derinlik sınırını artırıyoruz.
sys.setrecursionlimit(50000)

def unescape_wp_string(s):
    """WordPress'ten gelen stringlerdeki kaçış karakterlerini gerçek karakterlere çevirir."""
    if not isinstance(s, str):
        return s
    s = s.replace('\\n', '\n')
    s = s.replace('\\"', '"')
    s = s.replace('\\ ', ' ')
    s = s.replace('\\\\', '\\')
    return s

def patch_ast(obj, translations, visited=None):
    """AST ağacını derinlemesine tarar, döngüleri engeller ve ALT METİN (Substring) yöntemiyle çevirileri enjekte eder."""
    if visited is None:
        visited = set()
        
    # Basit veri tiplerini atla (İşlemi hızlandırır ve gereksiz taramayı önler)
    if obj is None or isinstance(obj, (int, float, str, bool, bytes)):
        return

    # Döngüsel referansları (Infinite Loop) önlemek için objenin kimliğini kaydet
    obj_id = id(obj)
    if obj_id in visited:
        return
    visited.add(obj_id)

    if isinstance(obj, list) or isinstance(obj, tuple):
        for item in obj:
            patch_ast(item, translations, visited)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            patch_ast(k, translations, visited)
            patch_ast(v, translations, visited)
    elif hasattr(obj, '__dict__'):
        class_name = type(obj).__name__
        
        # 1. Diyalog satırlarını bul ve ALT METİN olarak değiştir
        if class_name == 'Say' and hasattr(obj, 'what'):
            text = obj.what
            # Ren'Py v7 (Py2) desteklemek için bytes/str kontrolü yapıyoruz
            is_bytes = isinstance(text, bytes)
            text_str = text.decode('utf-8') if is_bytes else text
            
            original_text_str = text_str
            for k, v in translations.items():
                if k in text_str:
                    text_str = text_str.replace(k, v)
                    
            if text_str != original_text_str:
                obj.what = text_str.encode('utf-8') if is_bytes else text_str
                
        # 2. Menü (Seçim) Ekranlarını bul ve ALT METİN olarak değiştir
        elif class_name == 'Menu' and hasattr(obj, 'items'):
            new_items = []
            for item in obj.items:
                label = item[0]
                is_bytes = isinstance(label, bytes)
                label_str = label.decode('utf-8') if is_bytes else label
                
                original_label_str = label_str
                for k, v in translations.items():
                    if k in label_str:
                        label_str = label_str.replace(k, v)
                        
                if label_str != original_label_str:
                    label = label_str.encode('utf-8') if is_bytes else label_str
                    
                # Menü öğeleri genelde 3 parçadan oluşur (Etiket, Şart, Blok)
                if len(item) >= 3:
                    new_items.append((label, item[1], item[2]))
                else:
                    new_items.append((label,) + item[1:])
            obj.items = new_items
            
        # Alt objeleri taramaya devam et
        for k, v in obj.__dict__.items():
            patch_ast(v, translations, visited)

def process_rpyc_file(file_bytes, raw_translations):
    """Orijinal RPYC dosyasını açar, yamalar ve boyut kaymalarını hesaplayarak geri paketler."""
    
    # --- KRİTİK ADIM: Çevirileri Enjeksiyon İçin Kusursuz Hale Getir ---
    # Çakışmaları önlemek için önce en uzun cümleleri (karakter sayısına göre) sıralıyoruz
    sorted_keys = sorted(raw_translations.keys(), key=len, reverse=True)
    clean_translations = {}
    for k in sorted_keys:
        clean_k = unescape_wp_string(k)
        if clean_k.strip(): # Boşluk veya hatalı kelimeleri yoksay
            clean_translations[clean_k] = unescape_wp_string(raw_translations[k])

    # --- RPYC DOSYASINI AÇ VE YAMALA ---
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
        
        # Temizlenmiş ve sıralanmış çevirileri AST içine enjekte et
        patch_ast(ast_tree, clean_translations)
        
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        new_zlib = zlib.compress(new_pickle)
        
        new_file = bytearray(file_bytes[:position])
        current_offset = position
        
        for idx, chunk in enumerate(chunks):
            slot = chunk["slot"]
            if slot == 1:
                data_to_write = new_zlib
            else:
                data_to_write = file_bytes[chunk["start"] : chunk["start"] + chunk["length"]]
                
            dir_pos = 10 + (idx * 12)
            new_file[dir_pos : dir_pos+12] = struct.pack("III", slot, current_offset, len(data_to_write))
            
            new_file.extend(data_to_write)
            current_offset += len(data_to_write)
            
        return bytes(new_file)
        
    else:
        # Eski V1 Formatındaki (.rpyc) Oyunlar İçin
        raw_pickle = zlib.decompress(file_bytes)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        patch_ast(ast_tree, clean_translations)
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        return zlib.compress(new_pickle)
