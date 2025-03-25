from flask import Flask, request, render_template, jsonify
from werkzeug.utils import secure_filename
import os
import re
from datetime import datetime
import json
from collections import defaultdict, Counter
import zipfile
import io

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

def parse_whatsapp_chat(content):
    messages = []
    pattern = r'\[(\d{1,2}\.\d{1,2}\.\d{4}), (\d{1,2}:\d{1,2}:\d{2})\] ([^:]+): (.+)'
    
    for line in content.split('\n'):
        match = re.match(pattern, line)
        if match:
            date_str, time_str, sender, message = match.groups()
            
            # Skip system messages and media
            if any(skip in message for skip in ['<המדיה לא נכללה>', 'הוסיף/ה', 'יצר/ה את הקבוצה']):
                continue
                
            try:
                # Convert date format from DD.MM.YYYY to DD/MM/YYYY for parsing
                date_str = date_str.replace('.', '/')
                date_time = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M:%S")
                
                messages.append({
                    'date': date_time,
                    'sender': sender.strip(),
                    'message': message.strip()
                })
            except ValueError:
                continue
    
    if not messages:
        raise ValueError("לא נמצאו הודעות תקינות בקובץ")
    
    return messages

def calculate_statistics(messages):
    stats = {
        'total_messages': len(messages),
        'unique_senders': len(set(msg['sender'] for msg in messages)),
        'messages_per_sender': defaultdict(int),
        'messages_per_hour': defaultdict(int),
        'messages_per_day': defaultdict(int),
        'most_active_hour': 0,
        'average_message_length': 0,
        'date_range': {
            'start': min(msg['date'] for msg in messages).strftime('%d/%m/%Y'),
            'end': max(msg['date'] for msg in messages).strftime('%d/%m/%Y')
        }
    }
    
    total_length = 0
    for msg in messages:
        stats['messages_per_sender'][msg['sender']] += 1
        stats['messages_per_hour'][msg['date'].hour] += 1
        stats['messages_per_day'][msg['date'].strftime('%Y-%m-%d')] += 1
        total_length += len(msg['message'])
    
    # Convert defaultdict to regular dict for JSON serialization
    stats['messages_per_sender'] = dict(stats['messages_per_sender'])
    stats['messages_per_hour'] = dict(stats['messages_per_hour'])
    stats['messages_per_day'] = dict(stats['messages_per_day'])
    
    # Calculate most active hour
    stats['most_active_hour'] = max(stats['messages_per_hour'].items(), key=lambda x: x[1])[0]
    
    # Calculate average message length
    stats['average_message_length'] = round(total_length / len(messages), 1)
    
    return stats

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/help')
def help():
    return render_template('help.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'לא נבחר קובץ'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'לא נבחר קובץ'}), 400
    
    if not file.filename.endswith(('.txt', '.zip')):
        return jsonify({'error': 'פורמט קובץ לא נתמך. אנא העלה קובץ .txt או .zip'}), 400
    
    try:
        if file.filename.endswith('.zip'):
            with zipfile.ZipFile(file, 'r') as zip_ref:
                # Find the first .txt file in the zip
                txt_files = [f for f in zip_ref.namelist() if f.endswith('.txt')]
                if not txt_files:
                    return jsonify({'error': 'לא נמצא קובץ טקסט בקובץ ה-ZIP'}), 400
                
                # Read the first .txt file
                with zip_ref.open(txt_files[0]) as txt_file:
                    content = txt_file.read().decode('utf-8')
        else:
            content = file.read().decode('utf-8')
        
        messages = parse_whatsapp_chat(content)
        stats = calculate_statistics(messages)
        
        return jsonify({
            'success': True,
            'redirect': f'/dashboard?data={json.dumps(stats)}'
        })
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': 'אירעה שגיאה בעיבוד הקובץ. אנא נסה שוב'}), 400

if __name__ == '__main__':
    app.run(debug=True) 