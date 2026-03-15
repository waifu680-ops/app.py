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
# Decompiler'ın tam adresini (mutlak yol) alıyoruz
UNRPYC_PATH = os.path.abspath('unrpyc.py')

def unescape_wp_string(s):
    if not isinstance(s, str): return s
    return s.replace('\\n', '\n').replace('\\"', '"').replace('\\ ', ' ').replace('\\\\', '\\').replace('\r', '')

def detect_engine(file_bytes):
    try:
        if file_bytes.startswith(b"RENPY RPC2"):
            position = 10
            while True:
                slot, start, length = struct.unpack("III", file_bytes[position:position+12])
                if slot == 0: break
                if slot == 1:
                    chunk = file_bytes[start:start+length]
                    raw_pickle = zlib.decompress(chunk)
                    if raw_pickle[0] == 0x80 and raw_pickle[1] >= 4:
                        return RENPY_8_SH
                    return RENPY_7_SH
                position += 12
        else:
            raw_pickle = zlib.decompress(file_bytes)
            if raw_pickle[0] == 0x80 and raw_pickle[1] >= 4:
                return RENPY_8_SH
    except Exception:
        pass
    return RENPY_7_SH

# SİHİRLİ FONKSİYON: Dosya hangi alt klasöre saklanırsa saklansın bulup çıkarır!
def find_file_by_ext(directory, ext):
    for root, _, files in os.walk(directory):
        for f in files:
            if f.endswith(ext):
                return os.path.join(root, f)
    return None

@app.route('/decompile', methods=['POST'])
def decompile_rpyc():
    data = request.json
    if not data or 'filedata' not in data:
        return jsonify({"error": "Dosya verisi bulunamadı."}), 400

    req_id = str(uuid.uuid4())
    work_dir = os.path.abspath(f"temp_decomp_{req_id}")
    os.makedirs(work_dir, exist_ok=True)

    try:
        file_data = base64.b64decode(data['filedata'])
        filename = data.get('filename', 'temp.rpyc')
        
        rpyc_path = os.path.join(work_dir, filename)
        with open(rpyc_path, 'wb') as f:
            f.write(file_data)
            
        # Aracı çalıştır ve ne yaptığını kaydet
        comp_res = subprocess.run(['python', UNRPYC_PATH, filename], cwd=work_dir, capture_output=True, text=True)
        
        # Derinlemesine tarama yaparak oluşan .rpy dosyasını bul
        actual_rpy_path = find_file_by_ext(work_dir, '.rpy')
        
        if not actual_rpy_path:
            # Eğer cidden oluşmadıysa arabanın kaputunu açıp logları WordPress'e bas!
            raise Exception(f"Decompile işlemi başarısız: .rpy dosyası hiçbir klasörde bulunamadı!\nAraç Çıktısı: {comp_res.stdout}\nHata: {comp_res.stderr}")
            
        with open(actual_rpy_path, 'r', encoding='utf-8') as f:
            rpy_content = f.read()
            
        return jsonify({"success": True, "rpy_content": rpy_content})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir)

@app.route('/patch', methods=['POST'])
def patch_endpoint():
    data = request.get_json()
    if not data or 'filedata' not in data or 'translations' not in data:
        return jsonify({"error": "Eksik veri gönderildi."}), 400

    original_rpyc_bytes = base64.b64decode(data['filedata'])
    raw_translations = data['translations']

    CHOSEN_ENGINE = detect_engine(original_rpyc_bytes)
    clean_translations = {unescape_wp_string(k): unescape_wp_string(v) for k, v in raw_translations.items() if unescape_wp_string(k).strip()}

    req_id = str(uuid.uuid4())
    proj_dir = os.path.abspath(f"temp_project_{req_id}")
    game_dir = os.path.join(proj_dir, "game")
    os.makedirs(game_dir, exist_ok=True)

    try:
        filename = data.get('filename', 'script.rpyc')
        rpyc_path = os.path.join(game_dir, filename)

        with open(rpyc_path, 'wb') as f:
            f.write(original_rpyc_bytes)

        decomp_res = subprocess.run(['python', UNRPYC_PATH, filename], cwd=game_dir, capture_output=True, text=True)

        # Derinlemesine tarama ile bul
        actual_rpy_path = find_file_by_ext(game_dir, '.rpy')
        if not actual_rpy_path:
            raise Exception(f"Decompile başarısız.\nÇıktı: {decomp_res.stdout}\nHata: {decomp_res.stderr}")

        with open(actual_rpy_path, 'r', encoding='utf-8') as f:
            rpy_content = f.read()

        for eng, tur in clean_translations.items():
            rpy_content = rpy_content.replace(eng, tur)

        with open(actual_rpy_path, 'w', encoding='utf-8') as f:
            f.write(rpy_content)

        # Eski rpyc'yi sil ki yeni derlenenle karışmasın
        os.remove(rpyc_path)

        comp_res = subprocess.run([CHOSEN_ENGINE, proj_dir, 'compile'], capture_output=True, text=True)

        # Yeni derlenmiş rpyc'yi alt klasörlerde dahi olsa bul
        actual_rpyc_path = find_file_by_ext(game_dir, '.rpyc')
        if not actual_rpyc_path:
            full_error_msg = f"RenPy Resmi Motoru derleme yapamadı!\nMotor: {CHOSEN_ENGINE}\n\n--- DETAY ---\n{comp_res.stdout}\n{comp_res.stderr}"
            return jsonify({'error': full_error_msg}), 500

        with open(actual_rpyc_path, 'rb') as f:
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
