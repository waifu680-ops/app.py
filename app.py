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
        file_data = base64.b64decode(data['filedata'])
        filename = data.get('filename', 'temp.rpyc')
        
        with open(filename, 'wb') as f:
            f.write(file_data)
            
        # DİKKAT: Artık hatayı gizlemiyoruz, 'capture_output=True' ile yakalıyoruz!
        result = subprocess.run(['python', 'unrpyc.py', filename], capture_output=True, text=True)
        
        # Eğer unrpyc hata verirse (status 1), gerçek hatayı WordPress'e gönder
        if result.returncode != 0:
            if os.path.exists(filename): os.remove(filename)
            return jsonify({"error": f"unrpyc Detaylı Hata:\n{result.stderr}"}), 500
        
        rpy_filename = filename.replace('.rpyc', '.rpy')
        
        with open(rpy_filename, 'r', encoding='utf-8') as f:
            rpy_content = f.read()
            
        if os.path.exists(filename): os.remove(filename)
        if os.path.exists(rpy_filename): os.remove(rpy_filename)
        
        return jsonify({"success": True, "rpy_content": rpy_content})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
