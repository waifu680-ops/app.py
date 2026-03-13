import struct
import zlib
import pickletools

def unescape_wp_string(s):
    """WordPress'ten gelen metinleri düzeltir."""
    if not isinstance(s, str): return s
    s = s.replace('\\n', '\n')
    s = s.replace('\\"', '"')
    s = s.replace('\\ ', ' ')
    s = s.replace('\\\\', '\\')
    return s.replace('\r', '')

def binary_pickle_patch(pickle_bytes, translations):
    """
    Paketi HİÇ AÇMADAN (Pickle.loads kullanmadan) doğrudan makine 
    kodu üzerinde kelime avı yapar ve metinleri değiştirir.
    Kusursuz boyut ve çerçeve (Frame) hesaplaması yapar.
    """
    ops = list(pickletools.genops(pickle_bytes))
    edits = []
    frames = []
    
    for i, (opcode, arg, pos) in enumerate(ops):
        # Protocol 4 ve 5'teki boyut çerçevelerini yakala
        if opcode.name == 'FRAME':
            frames.append({"pos": pos + 1, "len": arg, "start_data": pos + 9})
            
        # Makine dilindeki metin kodlarını yakala
        elif opcode.name in ('SHORT_BINUNICODE', 'BINUNICODE', 'UNICODE', 'SHORT_BINSTRING', 'BINSTRING', 'BINBYTES'):
            if isinstance(arg, str):
                orig_text = arg
            else:
                try:
                    orig_text = arg.decode('utf-8')
                except:
                    continue # UTF-8 değilse atla
                    
            new_text = orig_text
            for k, v in translations.items():
                if k in new_text:
                    new_text = new_text.replace(k, v)
                    
            # Eğer kelime değiştiyse, boyut kaymasını (Delta) hesapla ve makine koduna enjekte et
            if new_text != orig_text:
                new_sdata = new_text.encode('utf-8')
                end_pos = ops[i+1][2] if i+1 < len(ops) else len(pickle_bytes)
                
                if opcode.name in ('SHORT_BINUNICODE', 'SHORT_BINSTRING'):
                    if len(new_sdata) <= 255:
                        new_bytes = bytes([pickle_bytes[pos], len(new_sdata)]) + new_sdata
                    else:
                        new_op = b'X' if opcode.name == 'SHORT_BINUNICODE' else b'T'
                        new_bytes = new_op + struct.pack("<I", len(new_sdata)) + new_sdata
                elif opcode.name in ('BINUNICODE', 'BINSTRING', 'BINBYTES'):
                    new_bytes = bytes([pickle_bytes[pos]]) + struct.pack("<I", len(new_sdata)) + new_sdata
                elif opcode.name == 'UNICODE':
                    new_escaped = new_text.encode('raw_unicode_escape')
                    new_bytes = b'V' + new_escaped + b'\n'
                
                edits.append({
                    "pos": pos,
                    "end": end_pos,
                    "new_bytes": new_bytes,
                    "delta": len(new_bytes) - (end_pos - pos)
                })
                
    # Hesaplanan değişiklikleri dosyaya sondan başa doğru yaz (Kaymaları önlemek için)
    data = bytearray(pickle_bytes)
    for edit in reversed(edits):
        data[edit["pos"]:edit["end"]] = edit["new_bytes"]
        if edit["delta"] != 0:
            for f in frames:
                if f["start_data"] <= edit["pos"]:
                    f["len"] += edit["delta"]
                    data[f["pos"] : f["pos"]+8] = struct.pack("<Q", f["len"])

    return bytes(data)

def process_rpyc_file(file_bytes, raw_translations):
    """Dosyayı %100 Orijinal formatında tutarak hem Kod hem Kaynak kısımlarını yamalar."""
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
                # SLOT 1: İkili Yama (Binary Patch)
                raw_pickle = zlib.decompress(chunk_data)
                patched_pickle = binary_pickle_patch(raw_pickle, clean_translations)
                payloads[1] = zlib.compress(patched_pickle)
                
            elif c["slot"] == 2:
                # SLOT 2: Kaynak Kod (Source Code) Yamalaması
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
        # Eski V1 Formatı
        raw_pickle = zlib.decompress(file_bytes)
        patched_pickle = binary_pickle_patch(raw_pickle, clean_translations)
        return zlib.compress(patched_pickle)
