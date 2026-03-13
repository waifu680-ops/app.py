import struct
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
                # Menü öğeleri genelde 3 parçadan oluşur (Etiket, Şart, Blok)
                if len(item) >= 3:
                    new_items.append((label, item[1], item[2]))
                else:
                    new_items.append((label,) + item[1:])
            obj.items = new_items
            
        # Alt objeleri taramaya devam et
        for k, v in obj.__dict__.items():
            patch_ast(v, translations)

def process_rpyc_file(file_bytes, translations):
    """Orijinal RPYC dosyasını açar, yamalar ve boyut kaymalarını hesaplayarak geri paketler."""
    if file_bytes.startswith(b"RENPY RPC2"):
        position = 10
        chunks = []
        
        # 1. RPYC Dosyasının İkili (Binary) Haritasını Çıkar
        while True:
            slot, start, length = struct.unpack("III", file_bytes[position:position+12])
            position += 12
            if slot == 0: # 0, haritanın bittiğini gösterir
                break
            chunks.append({"slot": slot, "start": start, "length": length})
            
        # 2. Asıl Oyun Kodlarının Bulunduğu "Slot 1"i Bul
        slot1 = next((c for c in chunks if c["slot"] == 1), None)
        if not slot1:
            raise ValueError("Geçerli bir kod bölümü (Slot 1) bulunamadı.")
            
        # 3. Zlib'den Çıkar ve Belleğe (AST) Yükle
        zlib_data = file_bytes[slot1["start"] : slot1["start"] + slot1["length"]]
        raw_pickle = zlib.decompress(zlib_data)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        
        # 4. Çevirileri Doğrudan Objelerin İçine Enjekte Et
        patch_ast(ast_tree, translations)
        
        # 5. Orijinal Formatta (Protocol 2) Yeniden Şifrele ve Zlib ile Sıkıştır
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        new_zlib = zlib.compress(new_pickle)
        
        # 6. YENİ RPYC DOSYASINI İNŞA ET
        # Yeni Türkçe metinler daha uzun/kısa olabileceği için ofset (başlangıç) noktalarını kaydırmalıyız
        new_file = bytearray(file_bytes[:position]) # Başlık ve orijinal harita (0 bitişi dahil)
        current_offset = position
        
        for idx, chunk in enumerate(chunks):
            slot = chunk["slot"]
            # Eğer Slot 1 ise kendi yamaladığımız veriyi, değilse orijinal veriyi koy
            if slot == 1:
                data_to_write = new_zlib
            else:
                data_to_write = file_bytes[chunk["start"] : chunk["start"] + chunk["length"]]
                
            # Dosya haritasındaki başlangıç ve uzunluk (offset/length) bilgilerini yeniden yaz
            dir_pos = 10 + (idx * 12)
            new_file[dir_pos : dir_pos+12] = struct.pack("III", slot, current_offset, len(data_to_write))
            
            # Veriyi dosyaya ekle ve ofseti bir sonraki slot için kaydır
            new_file.extend(data_to_write)
            current_offset += len(data_to_write)
            
        return bytes(new_file)
        
    else:
        # Eski V1 Formatındaki (.rpyc) Oyunlar İçin Alternatif
        raw_pickle = zlib.decompress(file_bytes)
        ast_tree = renpycompat.pickle_loads(raw_pickle)
        patch_ast(ast_tree, translations)
        new_pickle = pickle.dumps(ast_tree, protocol=2)
        return zlib.compress(new_pickle)
