import os
import subprocess
import uuid
import shutil
import base64
from flask import Flask, request, jsonify

app = Flask(__name__)

# Orijinal Ren'Py Motorunun yolu (Dockerfile içinde kurduğumuz yer)
RENPY_SH = os.environ.get('RENPY_DIR', '/renpy-8.1.3-sdk') + '/renpy.sh'

def unescape_wp_string(s):
    if not isinstance(s, str): return s
    s = s.replace('\\n', '\n')
    s = s.replace('\\"', '"')
    s = s.replace('\\ ', ' ')
    s = s.replace('\\\\', '\\')
    return s.replace('\r', '')

@app.route('/decompile', methods=['POST'])
def decompile_rpyc():
    """Editörün metinleri görebilmesi için dosyayı çözen uç nokta"""
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
    """YENİ NESİL ÇEVİRİ VE RESMİ DERLEME MOTORU"""
    data = request.get_json()
    if not data or 'filedata' not in data or 'translations' not in data:
        return jsonify({"error": "Eksik veri gönderildi."}), 400

    original_rpyc_bytes = base64.b64decode(data['filedata'])
    raw_translations = data['translations']

    # Kelimeleri uzunluklarına göre sırala (Önce uzun cümleler çevrilsin ki kısa kelimeler karışmasın)
    sorted_keys = sorted(raw_translations.keys(), key=len, reverse=True)
    clean_translations = {unescape_wp_string(k): unescape_wp_string(v) for k, v in raw_translations.items() if unescape_wp_string(k).strip()}

    # Her işlem için benzersiz bir klasör açıyoruz ki dosyalar çakışmasın
    req_id = str(uuid.uuid4())
    proj_dir = f"temp_project_{req_id}"
    game_dir = os.path.join(proj_dir, "game")
    os.makedirs(game_dir, exist_ok=True)

    try:
        rpyc_path = os.path.join(game_dir, "script.rpyc")
        rpy_path = os.path.join(game_dir, "script.rpy")

        # 1. Gelen orijinal dosyayı kaydet
        with open(rpyc_path, 'wb') as f:
            f.write(original_rpyc_bytes)

        # 2. Dosyayı düz metin (RPY) olarak çöz
        subprocess.run(['python', 'unrpyc.py', rpyc_path], check=True)

        # 3. Düz metni oku
        with open(rpy_path, 'r', encoding='utf-8') as f:
            rpy_content = f.read()

        # 4. KELİMELERİ BUL VE TERTEMİZ DEĞİŞTİR (Çökme riski sıfır!)
        for eng, tur in clean_translations.items():
            rpy_content = rpy_content.replace(eng, tur)

        # 5. Çevrilmiş metni tekrar kaydet
        with open(rpy_path, 'w', encoding='utf-8') as f:
            f.write(rpy_content)

        # 6. EN KRİTİK NOKTA: Orijinal RPYC'yi siliyoruz ki Ren'Py yedeği kullanmasın!
        os.remove(rpyc_path)

        # 7. SİHİR ZAMANI: Resmi Ren'Py SDK'sını çalıştırıp yeni dosyayı derliyoruz!
        comp_res = subprocess.run([RENPY_SH, proj_dir, 'compile'], capture_output=True, text=True)

        if not os.path.exists(rpyc_path):
            return jsonify({'error': 'RenPy Resmi Motoru derleme yapamadı!', 'log': comp_res.stdout + comp_res.stderr}), 500

        # 8. Ren'Py'ın kendi elleriyle oluşturduğu Orijinal/Hash-Onaylı dosyayı al
        with open(rpyc_path, 'rb') as f:
            new_rpyc_bytes = f.read()

        return jsonify({
            'success': True,
            'patched_file': base64.b64encode(new_rpyc_bytes).decode('utf-8')
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        # Sunucuyu temiz tutmak için geçici projeyi sil
        if os.path.exists(proj_dir):
            shutil.rmtree(proj_dir)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
