import os
import subprocess
import uuid
import shutil
import base64
import struct
import zlib
from flask import Flask, request, jsonify

app = Flask(__name__)

# Çift Çekirdekli Motor Yolları
RENPY_7_SH = '/renpy-7.5.3-sdk/renpy.sh'
RENPY_8_SH = '/renpy-8.1.3-sdk/renpy.sh'

def unescape_wp_string(s):
    if not isinstance(s, str): return s
    s = s.replace('\\n', '\n')
    s = s.replace('\\"', '"')
    s = s.replace('\\ ', ' ')
    s = s.replace('\\\\', '\\')
    return s.replace('\r', '')

def detect_engine(file_bytes):
    """Dosyanın şifreleme protokolüne bakarak Ren'Py 7 mi 8 mi olduğunu anlayan Dedektör"""
    try:
        if file_bytes.startswith(b"RENPY RPC2"):
            position = 10
            while True:
                slot, start, length = struct.unpack("III", file_bytes[position:position+12])
                if slot == 0: break
                if slot == 1:
                    chunk = file_bytes[start:start+length]
                    raw_pickle = zlib.decompress(chunk)
                    # Pickle V4 veya V5 ise kesinlikle Python 3 (Ren'Py 8) kullanıyordur
                    if raw_pickle[0] == 0x80 and raw_pickle[1] >= 4:
                        print("🤖 DEDEKTÖR: Yeni nesil Ren'Py 8 tespit edildi!")
                        return RENPY_8_SH
                    else:
                        print("🤖 DEDEKTÖR: Eski nesil Ren'Py 7 tespit edildi!")
                        return RENPY_7_SH
                position += 12
        else:
            raw_pickle = zlib.decompress(file_bytes)
            if raw_pickle[0] == 0x80 and raw_pickle[1] >= 4:
                print("🤖 DEDEKTÖR: Yeni nesil Ren'Py 8 tespit edildi! (V1 Format)")
                return RENPY_8_SH
    except Exception as e:
        print(f"Dedektör hatası: {e}. Varsayılan olarak Ren'Py 7 kullanılıyor.")
        
    print("🤖 DEDEKTÖR: Varsayılan olarak Ren'Py 7 tespit edildi!")
    return RENPY_7_SH

@app.route('/decompile', methods=['POST'])
def decompile_rpyc():
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
    data = request.get_json()
    if not data or 'filedata' not in data or 'translations' not in data:
        return jsonify({"error": "Eksik veri gönderildi."}), 400

    original_rpyc_bytes = base64.b64decode(data['filedata'])
    raw_translations = data['translations']

    # Gelen dosyanın hangi motorla derlenmesi gerektiğini otomatik bul!
    CHOSEN_ENGINE = detect_engine(original_rpyc_bytes)

    sorted_keys = sorted(raw_translations.keys(), key=len, reverse=True)
    clean_translations = {unescape_wp_string(k): unescape_wp_string(v) for k, v in raw_translations.items() if unescape_wp_string(k).strip()}

    req_id = str(uuid.uuid4())
    proj_dir = f"temp_project_{req_id}"
    game_dir = os.path.join(proj_dir, "game")
    os.makedirs(game_dir, exist_ok=True)

    try:
        rpyc_path = os.path.join(game_dir, "script.rpyc")
        rpy_path = os.path.join(game_dir, "script.rpy")

        with open(rpyc_path, 'wb') as f:
            f.write(original_rpyc_bytes)

        subprocess.run(['python', 'unrpyc.py', rpyc_path], check=True)

        with open(rpy_path, 'r', encoding='utf-8') as f:
            rpy_content = f.read()

        for eng, tur in clean_translations.items():
            rpy_content = rpy_content.replace(eng, tur)

        with open(rpy_path, 'w', encoding='utf-8') as f:
            f.write(rpy_content)

        os.remove(rpyc_path)

        # Doğru motoru (CHOSEN_ENGINE) kullanarak derleme yap!
        comp_res = subprocess.run([CHOSEN_ENGINE, proj_dir, 'compile'], capture_output=True, text=True)

        if not os.path.exists(rpyc_path):
            full_error_msg = f"RenPy Resmi Motoru derleme yapamadı!\nKullanılan Motor: {CHOSEN_ENGINE}\n\n--- HATA DETAYI ---\n{comp_res.stdout}\n{comp_res.stderr}"
            print(full_error_msg)
            return jsonify({'error': full_error_msg}), 500

        with open(rpyc_path, 'rb') as f:
            new_rpyc_bytes = f.read()

        return jsonify({
            'success': True,
            'patched_file': base64.b64encode(new_rpyc_bytes).decode('utf-8')
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(proj_dir):
            shutil.rmtree(proj_dir)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
