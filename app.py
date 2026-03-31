import os
import subprocess
import shutil
import base64
import struct
import zlib
import pickle
import sys
import traceback
from flask import Flask, request, jsonify
from decompiler import magic, renpycompat

sys.setrecursionlimit(50000)

app = Flask(__name__)

# --- 1. YARDIMCI VE ZIRHLI ÇEVİRİ FONKSİYONLARI ---
def unescape_wp_string(s):
    if not isinstance(s, str): return s
    s = s.replace('\\n', '\n')
    s = s.replace('\\"', '"')
    s = s.replace('\\ ', ' ')
    s = s.replace('\\\\', '\\')
    return s.replace('\r', '')

def apply_translation(text, translations):
    """AST Zırhı: Sadece birebir eşleşen metinleri değiştirir. 'at' kelimesi kodları bozamaz."""
    if not isinstance(text, (str, bytes)): return text
    is_bytes = isinstance(text, bytes)
    text_str = text.decode('utf-8', 'ignore') if is_bytes else text
    
    for k, v in translations.items():
        # Başındaki ve sonundaki tırnakları temizleyerek saf eşleşme ara
        clean_k = k[1:-1] if len(k) >= 2 and k[0] in ('"', "'") and k[-1] == k[0] else k
        clean_v = v[1:-1] if len(v) >= 2 and v[0] in ('"', "'") and v[-1] == v[0] else v
        
        if text_str == clean_k:
            return clean_v.encode('utf-8') if is_bytes else clean_v
    return text

# --- 2. AST MOTORU (Düz Metni Bozmadan Sadece Hedefi Vurur) ---
def patch_ast(obj, translations, visited=None):
    if visited is None: visited = set()
    if obj is None or isinstance(obj, (int, float, bool, str, bytes)): return
    
    obj_id = id(obj)
    if obj_id in visited: return
    visited.add(obj_id)

    if isinstance(obj, list):
        for i in range(len(obj)):
            if isinstance(obj[i], (str, bytes)):
                obj[i] = apply_translation(obj[i], translations)
            else:
                patch_ast(obj[i], translations, visited)
    elif isinstance(obj, tuple):
        for item in obj:
            patch_ast(item, translations, visited)
    elif isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, (str, bytes)):
                obj[k] = apply_translation(v, translations)
            else:
                patch_ast(v, translations, visited)
    elif hasattr(obj, '__dict__'):
        class_name = type(obj).__name__
        
        # Ren'Py Sınıflarına Göre Filtre
        if class_name in ('Say', 'TranslateSay') and hasattr(obj, 'what'):
            obj.what = apply_translation(obj.what, translations)
        elif class_name in ('Menu', 'TranslateMenu') and hasattr(obj, 'items'):
            new_items = []
            for item in obj.items:
                label = apply_translation(item[0], translations)
                new_items.append((label, item[1], item[2]) if len(item) >= 3 else (label,) + item[1:])
            obj.items = new_items
        elif class_name == 'TranslateString' and hasattr(obj, 'new'):
            obj.new = apply_translation(obj.new, translations)
            
        FORBIDDEN_KEYS = {'old', 'language', 'identifier', 'filename', 'name', 'label', 'parameters'}
        for k, v in list(obj.__dict__.items()):
            if k in FORBIDDEN_KEYS: continue
            if class_name in ('Say', 'TranslateSay') and k == 'what': continue
            if class_name in ('Menu', 'TranslateMenu') and k == 'items': continue
            if class_name == 'TranslateString' and k == 'new': continue
            
            if isinstance(v, (str, bytes)):
                text_len = len(v) if isinstance(v, str) else len(v.decode('utf-8', 'ignore'))
                if text_len >= 2:
                    setattr(obj, k, apply_translation(v, translations))
            else:
                patch_ast(v, translations, visited)

# --- 3. ENDPOINT'LER ---
@app.route('/decompile', methods=['POST'])
def decompile_rpyc():
    """Okuma kısmı eskisi gibi Unrpyc kullanılarak düz metne dönüştürülür."""
    data = request.json
    if not data or 'filedata' not in data:
        return jsonify({"error": "Dosya verisi bulunamadı."}), 400

    try:
        file_data = base64.b64decode(data['filedata'])
        filename = data.get('filename', 'temp.rpyc')
        rpy_filename = filename.replace('.rpyc', '.rpy')
        
        with open(filename, 'wb') as f:
            f.write(file_data)
            
        subprocess.run(['python', 'unrpyc.py', filename], check=True)
        
        with open(rpy_filename, 'r', encoding='utf-8') as f:
            rpy_content = f.read()
            
        if os.path.exists(filename): os.remove(filename)
        if os.path.exists(rpy_filename): os.remove(rpy_filename)
        
        return jsonify({"success": True, "rpy_content": rpy_content})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/patch', methods=['POST'])
def patch_endpoint():
    """Derleme (Compile) işlemi kaldırıldı. Orijinal dosya doğrudan güvenle yamalanır."""
    data = request.get_json()
    if not data or 'filedata' not in data or 'translations' not in data:
        return jsonify({"error": "Eksik veri gönderildi."}), 400

    try:
        file_bytes = base64.b64decode(data['filedata'])
        raw_translations = data['translations']

        # Sadece geçerli ve değiştirilmiş çevirileri al
        clean_translations = {}
        for k, v in raw_translations.items():
            clean_k = unescape_wp_string(k)
            clean_v = unescape_wp_string(v)
            if clean_k.strip() and clean_k != clean_v:
                clean_translations[clean_k] = clean_v

        # --- AST MOTORU İLE DOĞRUDAN PAKETLEME (Çökme %0) ---
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
                    payloads[1] = zlib.compress(pickle.dumps(ast_tree, protocol=orig_proto))
                elif c["slot"] == 2:
                    try:
                        # Slot 2 loglarına giren 'at' komutlarını engelle
                        raw_source = zlib.decompress(chunk_data).decode('utf-8')
                        for k, v in clean_translations.items():
                            if '"' in k or "'" in k:
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
                
            new_rpyc_bytes = bytes(new_file)
        else:
            raw_pickle = zlib.decompress(file_bytes)
            orig_proto = 2
            if len(raw_pickle) >= 2 and raw_pickle[0] == 0x80:
                orig_proto = raw_pickle[1]
            ast_tree = renpycompat.pickle_loads(raw_pickle)
            patch_ast(ast_tree, clean_translations)
            new_rpyc_bytes = zlib.compress(pickle.dumps(ast_tree, protocol=orig_proto))

        return jsonify({
            'success': True,
            'patched_file': base64.b64encode(new_rpyc_bytes).decode('utf-8')
        })

    except Exception as e:
        error_msg = traceback.format_exc()
        print("PATCH ERROR:", error_msg)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
