import json
import zlib
import pickle
from decompiler import magic, renpycompat

def patch_ast(obj, translations):
    """AST ağacını derinlemesine tarar ve çevirileri orijinal objelere enjekte eder."""
    if isinstance(obj, list) or isinstance(obj, tuple):
        for item in obj:
            patch_ast(item, translations)
    elif hasattr(obj, '__dict__'):
        class_name = type(obj).__name__
        
        # Diyalog satırlarını bul ve değiştir
        if class_name == 'Say' and hasattr(obj, 'what'):
            if obj.what in translations:
                obj.what = translations[obj.what]
                
        # Menü seçimlerini bul ve değiştir
        elif class_name == 'Menu' and hasattr(obj, 'items'):
            new_items = []
            for item in obj.items:
                label = item[0]
                if label in translations:
                    label = translations[label]
                new_items.append((label, item[1], item[2]))
            obj.items = new_items
            
        # Alt objeleri taramaya devam et
        for k, v in obj.__dict__.items():
            patch_ast(v, translations)

def process_rpyc_file(file_bytes, translations):
    """Orijinal RPYC dosyasını açar, yamalar ve bozulmadan geri paketler."""
    if file_bytes.startswith(b"RENPY RPC2 SAVE FILE COLUMN FORMAT\n"):
        # RPC2 başlığını atla
        pos = file_bytes.find(b"\n", 35) + 1
        
        # Zlib ile sıkıştırılmış JSON Slot Haritasını oku
        decomp = zlib.decompressobj()
        slot_json_bytes = decomp.decompress(file_bytes[pos:])
        slot_map = json.loads(slot_json_bytes.decode('utf-8'))
        
        # Slot verisinin başladığı yeri bul
        slot_data_start = len(file_bytes) - len(decomp.unused_data)
        
        if 'script' not in slot_map:
            return file_bytes # Geçerli bir script slotu yoksa orijinali döndür
        
        offset, length = slot_map['script']
        zlib_data = file_bytes[slot_data_start + offset : slot_data_start + offset + length]
        
        # Makine dilini (AST) belleğe yükle
        raw_pickle = zlib.decompress(zlib_data)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        
        # Çevirileri Enjekte Et
        patch_ast(ast_tree, translations)
        
        # Yeniden Şifrele ve Sıkıştır (Zorunlu Protocol 2)
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        new_zlib = zlib.compress(new_pickle)
        
        # Yeni dosya uzunluklarına göre Slot Haritasını güncelle
        slot_map['script'] = [0, len(new_zlib)]
        new_slot_json = json.dumps(slot_map).encode('utf-8')
        new_slot_zlib = zlib.compress(new_slot_json)
        
        # Orijinal RPYC formatını sıfırdan inşa et
        header = b"RENPY RPC2 SAVE FILE COLUMN FORMAT\n"
        return header + new_slot_zlib + new_zlib
    else:
        # Eski V1 Formatı (Sadece zlib datası)
        raw_pickle = zlib.decompress(file_bytes)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        patch_ast(ast_tree, translations)
        return zlib.compress(pickle.dumps(ast_tree, protocol=2))
