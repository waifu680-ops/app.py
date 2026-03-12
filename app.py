from flask import Flask, request, jsonify
import base64
import os
import subprocess

app = Flask(__name__)

@app.route('/decompile', methods=['POST'])
def decompile_rpyc():
    data = request.json
    if not data or 'filedata' not in data:
        return jsonify({"error": "Dosya verisi bulunamadı."}), 400

    try:
        # WordPress'ten gelen Base64 şifreli dosyayı çöz ve kaydet
        file_data = base64.b64decode(data['filedata'])
        filename = data.get('filename', 'temp.rpyc')
        
        with open(filename, 'wb') as f:
            f.write(file_data)
            
        # unrpyc aracını çalıştırarak .rpyc dosyasını .rpy formatına çevir
        subprocess.run(['python', 'unrpyc.py', filename], check=True)
        
        rpy_filename = filename.replace('.rpyc', '.rpy')
        
        # Çevrilen .rpy dosyasını oku
        with open(rpy_filename, 'r', encoding='utf-8') as f:
            rpy_content = f.read()
            
        # Sunucuda yer kaplamaması için geçici dosyaları sil
        if os.path.exists(filename): os.remove(filename)
        if os.path.exists(rpy_filename): os.remove(rpy_filename)
        
        return jsonify({"success": True, "rpy_content": rpy_content})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
