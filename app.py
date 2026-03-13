from flask import Flask, request, jsonify
import base64
import os
import subprocess
from rpyc_patcher import process_rpyc_file

app = Flask(__name__)

@app.route('/decompile', methods=['POST'])
def decompile_rpyc():
    data = request.json
    if not data or 'filedata' not in data:
        return jsonify({"error": "Dosya verisi bulunamadı."}), 400

    try:
        file_data = base64.b64decode(data['filedata'])
        filename = data.get('filename', 'temp.rpyc')
        
        with open(filename, 'wb') as f:
            f.write(file_data)
            
        # unrpyc.py'yi çalıştır ve tüm çıktıları (stdout ve stderr) yakala
        result = subprocess.run(['python', 'unrpyc.py', filename], capture_output=True, text=True)
        
        rpy_filename = filename.replace('.rpyc', '.rpy')
        
        # EĞER DOSYA OLUŞMADIYSA UNRPYC SESSİZCE HATA VERMİŞTİR!
        if not os.path.exists(rpy_filename):
            if os.path.exists(filename): os.remove(filename)
            # Gerçek hatayı görmek için WordPress'e Log'u fırlat!
            return jsonify({
                "error": f"unrpyc dosyayı çözemedi!\n\n--- UNRPYC LOG ---\n{result.stdout}\n{result.stderr}"
            }), 500
        
        with open(rpy_filename, 'r', encoding='utf-8') as f:
            rpy_content = f.read()
            
        if os.path.exists(filename): os.remove(filename)
        if os.path.exists(rpy_filename): os.remove(rpy_filename)
        
        return jsonify({"success": True, "rpy_content": rpy_content})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/patch', methods=['POST'])
def patch_endpoint():
    try:
        data = request.get_json()
        if not data or 'filedata' not in data or 'translations' not in data:
            return jsonify({"error": "Eksik veri gönderildi."}), 400

        original_rpyc_bytes = base64.b64decode(data['filedata'])
        translations_dict = data['translations']
        
        patched_rpyc_bytes = process_rpyc_file(original_rpyc_bytes, translations_dict)
        
        return jsonify({
            'success': True,
            'patched_file': base64.b64encode(patched_rpyc_bytes).decode('utf-8')
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
