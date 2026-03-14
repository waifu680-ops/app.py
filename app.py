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

# Çift Çekirdekli Motor Yolları
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
    """Metnin hassas veri (kod, ses, resim, dosya yolu) olup olmadığını anlayan Yapay Zeka filtresi"""
    lower_text = text.lower()
    # Dosya uzantıları
    if any(ext in lower_text for ext in ['.mp3', '.ogg', '.wav', '.png', '.jpg', '.webp', '.webm', '.rpy']):
        return True
    # Dosya yolları (Klasör işaretleri)
    if '/' in text or '\\' in text:
        return True
    # Çok kısa teknik veriler (1-2 harf)
    if len(text) <= 2:
        return True
    # Sadece harf/sayı/alt tire içeren ve hiç boşluk barındırmayan kod değişkenleri
    if re.match(r'^[a-zA-Z0-9_]+$', text):
        return True
    return False

def extract_strings_from_rpy(rpy_content):
    """RPY dosyasındaki tüm metinleri sırasıyla ve kimlikleriyle (ID) çeker"""
    pattern = re.compile(r'"((?:\\.|[^"\\])*)"')
    extracted = []
    for match in pattern.finditer(rpy_content):
        s = match.group(1)
        if s not in extracted:
            extracted.append(s)
    return extracted

def parse_edited_text(edited_text):
    """Büyük kutudaki metni analiz eder, satır hatalarını ve boşlukları denetler!"""
    lines = edited_text.replace('\r', '').split('\n')
    translations = {}
    current_id = None
    current_text = []

    for line_num, line in enumerate(lines, 1):
        if line.startswith("---------"):
            # Önceki bloğu kaydet
            if current_id is not None:
                joined_text = "\n".join(current_text).strip()
                if not joined_text:
                    raise ValueError(f"Satır {line_num} Hatası: {current_id} numaralı ID'nin içi boş bırakılamaz!")
                translations[current_id] = joined_text
            
            # Yeni ID'yi al
            id_str = line.replace("---------", "").strip()
            if not id_str.isdigit():
                raise ValueError(f"Satır {line_num} Hatası: '---------' işaretinden sonra sadece sayısal bir ID gelmelidir! (Bozuk kısım: {id_str})")
            
            current_id = int(id_str)
            current_text = []
        else:
            # Kullanıcı ID etiketleri dışında bir yere yazı yazmışsa HATA ver
            if current_id is None:
                if line.strip():
                    raise ValueError(f"Satır {line_num} Hatası: Çeviri metinleri (Diyaloglar) sadece '---------' etiketlerinin altına yazılabilir. Kural dışı alan!")
            else:
                current_text.append(line)
    
    # Son bloğu da kaydet
    if current_id is not None:
        joined_text = "\n".join(current_text).strip()
        if not joined_text:
            raise ValueError(f"Satır Hata: {current_id} numaralı ID'nin içi boş bırakılamaz!")
        translations[current_id] = joined_text
        
    return translations

@app.route('/decompile', methods=['POST'])
def decompile_rpyc():
    data = request.json
    if not data or 'filedata' not in data:
        return jsonify({"error": "Dosya verisi bulunamadı."}), 400

    # Frontend'den Mod'u al (Varsayılan: "normal")
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
        
        # SİHİRLİ KISIM: Formatı oluştur!
        extracted = extract_strings_from_rpy(rpy_content)
        output_lines = []
        
        for i, text in enumerate(extracted):
            # Eğer mod Normalse ve metin hassas bir kodsa, bunu gizle!
            if mode == 'normal' and is_sensitive(text):
                continue
            
            # Kutu formatı için görünmez \n kodlarını gerçek satır atlamaya çevir
            display_text = text.replace('\\n', '\n')
            
            output_lines.append(f"---------{i}")
            output_lines.append(display_text)
            
        formatted_text = "\n".join(output_lines)
        
        # Artık frontend'e devasa bir RPY değil, tam istediğin tertemiz kutu formatı gidiyor
        return jsonify({"success": True, "formatted_text": formatted_text})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/patch', methods=['POST'])
def patch_endpoint():
    data = request.get_json()
    if not data or 'filedata' not in data or 'edited_text' not in data:
        return jsonify({"error": "Eksik veri gönderildi. (filedata ve edited_text gerekli)"}), 400

    mode = data.get('mode', 'normal')
    original_rpyc_bytes = base64.b64decode(data['filedata'])
    edited_text = data['edited_text']

    # 1. Aşama: Akıllı Hata Denetimi!
    try:
        translations = parse_edited_text(edited_text)
    except ValueError as ve:
        # Eğer kullanıcı satır kaydırdıysa, direkt satır numarasıyla hata fırlat
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

        # 2. Aşama: Çevirileri Doğrulama ve Yerleştirme
        for uid, tur_text in translations.items():
            if uid >= len(extracted):
                return jsonify({"error": f"Güvenlik İhlali: {uid} kimliği oyun dosyasıyla eşleşmiyor. Dosya dışarıdan bozulmuş olabilir!"}), 400
                
            eng_text = extracted[uid]
            
            # Normal Mod koruması! Adam kurnazlık yapıp normal moda script ID'si yapıştırırsa REDDET!
            if mode == 'normal' and is_sensitive(eng_text):
                return jsonify({"error": f"İhlal: {uid} numaralı ID oyunun hassas kodlarını (ses/resim/script) içeriyor ancak 'Normal Diyalog' modu seçili! Lütfen Hassas Modu açın."}), 400
            
            # Kullanıcının bastığı Enter tuşlarını yeniden kod diline (\n) çevir
            tur_text_escaped = tur_text.replace('\n', '\\n')
            
            if eng_text != tur_text_escaped:
                # Sadece tam kelimeyi güvenli bir şekilde değiştir
                rpy_content = rpy_content.replace(f'"{eng_text}"', f'"{tur_text_escaped}"')

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
