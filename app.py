import os
import subprocess
import uuid
import shutil
import base64
import struct
import zlib
import glob
from flask import Flask, request, jsonify

app = Flask(__name__)

# Çift Çekirdekli Motor Yolları
RENPY_7_SH = '/renpy-7.5.3-sdk/renpy.sh'
RENPY_8_SH = '/renpy-8.1.3-sdk/renpy.sh'
# Decompiler'ın tam adresini (mutlak yol) alıyoruz ki klasör değiştirince kaybolmasın
UNRPYC_PATH = os.path.abspath('unrpyc.py')

def unescape_wp_string(s):
    if not isinstance(s, str): return s
    s = s.replace('\\n', '\n')
    s = s.replace('\\"', '"')
    s = s.replace('\\ ', ' ')
    s = s.replace('\\\\', '\\')
    return s.replace('\r', '')

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
            
        # SİHİRLİ DOKUNUŞ: cwd=work_dir ile aracı sadece o klasörün içinde çalışmaya zorluyoruz!
        subprocess.run(['python', UNRPYC_PATH, filename], cwd=work_dir, check=True)
        
        # Artık dosyanın o klasörde oluştuğundan %100 eminiz
        rpy_files = glob.glob(os.path.join(work_dir, "*.rpy"))
        if not rpy_files:
            raise Exception("Decompile işlemi başarısız: .rpy dosyası oluşturulamadı.")
            
        with open(rpy_files[0], 'r', encoding='utf-8') as f:
            rpy_content = f.read()
            
        return jsonify({"success": True, "rpy_content": rpy_content})
        
    except subprocess.CalledProcessError as cpe:
        return jsonify({"error": f"Decompile aracı çöktü: {str(cpe)}"}), 500
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

        # Aracı yine klasör içine hapsediyoruz
        subprocess.run(['python', UNRPYC_PATH, filename], cwd=game_dir, check=True)

        rpy_files = glob.glob(os.path.join(game_dir, "*.rpy"))
        if not rpy_files:
            raise Exception("Decompile işlemi başarısız: .rpy dosyası oluşturulamadı.")
        
        actual_rpy_path = rpy_files[0]

        with open(actual_rpy_path, 'r', encoding='utf-8') as f:
            rpy_content = f.read()

        for eng, tur in clean_translations.items():
            rpy_content = rpy_content.replace(eng, tur)

        with open(actual_rpy_path, 'w', encoding='utf-8') as f:
            f.write(rpy_content)

        os.remove(rpyc_path)

        comp_res = subprocess.run([CHOSEN_ENGINE, proj_dir, 'compile'], capture_output=True, text=True)

        rpyc_files = glob.glob(os.path.join(game_dir, "*.rpyc"))
        if not rpyc_files:
            full_error_msg = f"RenPy Resmi Motoru derleme yapamadı!\nMotor: {CHOSEN_ENGINE}\n\n--- DETAY ---\n{comp_res.stdout}\n{comp_res.stderr}"
            return jsonify({'error': full_error_msg}), 500

        with open(rpyc_files[0], 'rb') as f:
            new_rpyc_bytes = f.read()

        return jsonify({
            'success': True,
            'patched_file': base64.b64encode(new_rpyc_bytes).decode('utf-8')
        })

    except subprocess.CalledProcessError as cpe:
        return jsonify({"error": f"İşlem çöktü: {str(cpe)}"}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(proj_dir):
            shutil.rmtree(proj_dir)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
