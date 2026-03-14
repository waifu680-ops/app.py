import os
import subprocess
import uuid
import shutil
import base64
import struct
import zlib
import re
from flask import Flask, request, jsonify

app = Flask(__name__)

RENPY_7_SH = '/renpy-7.5.3-sdk/renpy.sh'
RENPY_8_SH = '/renpy-8.1.3-sdk/renpy.sh'

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
                    else:
                        return RENPY_7_SH
                position += 12
        else:
            raw_pickle = zlib.decompress(file_bytes)
            if raw_pickle[0] == 0x80 and raw_pickle[1] >= 4:
                return RENPY_8_SH
    except Exception:
        pass
    return RENPY_7_SH

def is_sensitive(text):
    lower_text = text.lower()
    if any(ext in lower_text for ext in ['.mp3', '.ogg', '.wav', '.png', '.jpg', '.webp', '.webm', '.rpy']):
        return True
    if '/' in text or '\\' in text:
        return True
    if len(text) <= 2:
        return True
    if re.match(r'^[a-zA-Z0-9_]+$', text):
        return True
    return False

def extract_strings_from_rpy(rpy_content):
    # Hem çift tırnak hem tek tırnak içindeki yazıları ve boş olmayanları yakalar
    pattern = re.compile(r'(["\'])((?:\\\1|(?!\1).)+)\1')
    extracted = []
    for match in pattern.finditer(rpy_content):
        s = match.group(2)
        if s not in extracted:
            extracted.append(s)
    return extracted

def parse_edited_text(edited_text):
    lines = edited_text.replace('\r', '').split('\n')
    translations = {}
    current_id = None
    current_text = []

    for line_num, line in enumerate(lines, 1):
        if line.startswith("---------"):
            if current_id is not None:
                joined_text = "\n".join(current_text).strip()
                if not joined_text:
                    raise ValueError(f"Satır {line_num} Hatası: {current_id} numaralı ID'nin içi boş bırakılamaz!")
                translations[current_id] = joined_text
            
            id_str = line.replace("---------", "").strip()
            if not id_str.isdigit():
                raise ValueError(f"Satır {line_num} Hatası: '---------' işaretinden sonra sadece sayısal bir ID gelmelidir! (Bozuk kısım: {id_str})")
            
            current_id = int(id_str)
            current_text = []
        else:
            if current_id is None:
                if line.strip():
                    raise ValueError(f"Satır {line_num} Hatası: Çeviri metinleri sadece '---------' etiketlerinin altına yazılabilir!")
            else:
                current_text.append(line)
    
    if current_id is not None:
        joined_text = "\n".join(current_text).strip()
        if not joined_text:
            raise ValueError(f"Satır Hatası: {current_id} numaralı ID'nin içi boş bırakılamaz!")
        translations[current_id] = joined_text
        
    return translations

@app.route('/decompile', methods=['POST'])
def decompile_rpyc():
    data = request.json
    if not data or 'filedata' not in data:
        return jsonify({"error": "Dosya verisi bulunamadı."}), 400

    mode = data.get('mode', 'normal')

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
        
        extracted = extract_strings_from_rpy(rpy_content)
        output_lines = []
        
        for i, text in enumerate(extracted):
            if mode == 'normal' and is_sensitive(text):
                continue
            
            display_text = text.replace('\\n', '\n')
            output_lines.append(f"---------{i}")
            output_lines.append(display_text)
            
        formatted_text = "\n".join(output_lines)
        
        # SİHİRLİ DOKUNUŞ: WordPress eklentisi boş kalmasın diye adını "rpy_content" olarak geri çevirdik!
        return jsonify({"success": True, "rpy_content": formatted_text})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/patch', methods=['POST'])
def patch_endpoint():
    data = request.get_json()
    
    # WordPress eklentisinin eski veya yeni veri gönderme ihtimaline karşı akıllı yakalayıcı
    edited_text = data.get('edited_text')
    if not edited_text and 'translations' in data:
        if isinstance(data['translations'], str):
            edited_text = data['translations']

    if not data or 'filedata' not in data or not edited_text:
        return jsonify({"error": "Eksik veri gönderildi."}), 400

    mode = data.get('mode', 'normal')
    original_rpyc_bytes = base64.b64decode(data['filedata'])

    try:
        translations = parse_edited_text(edited_text)
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400

    CHOSEN_ENGINE = detect_engine(original_rpyc_bytes)
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

        extracted = extract_strings_from_rpy(rpy_content)

        for uid, tur_text in translations.items():
            if uid >= len(extracted):
                return jsonify({"error": f"Güvenlik İhlali: {uid} kimliği oyun dosyasıyla eşleşmiyor."}), 400
                
            eng_text = extracted[uid]
            
            if mode == 'normal' and is_sensitive(eng_text):
                return jsonify({"error": f"İhlal: {uid} numaralı ID oyunun hassas kodlarını içeriyor ancak 'Normal' mod seçili!"}), 400
            
            tur_text_escaped = tur_text.replace('\n', '\\n')
            
            if eng_text != tur_text_escaped:
                # Hem çift tırnak hem tek tırnak ihtimaline karşı güvenli yerleştirme
                rpy_content = rpy_content.replace(f'"{eng_text}"', f'"{tur_text_escaped}"')
                rpy_content = rpy_content.replace(f"'{eng_text}'", f"'{tur_text_escaped}'")

        with open(rpy_path, 'w', encoding='utf-8') as f:
            f.write(rpy_content)

        os.remove(rpyc_path)

        comp_res = subprocess.run([CHOSEN_ENGINE, proj_dir, 'compile'], capture_output=True, text=True)

        if not os.path.exists(rpyc_path):
            full_error_msg = f"Derleme Hatası!\n\n--- DETAY ---\n{comp_res.stdout}\n{comp_res.stderr}"
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
