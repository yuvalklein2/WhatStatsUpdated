from flask import Flask, request, render_template, jsonify, send_from_directory, session, redirect
from werkzeug.utils import secure_filename
import os
import re
from datetime import datetime
import json
from collections import defaultdict, Counter
import zipfile
import io
import secrets

app = Flask(__name__, 
           template_folder=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'templates')),
           static_folder=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'static')))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'public'))
app.secret_key = secrets.token_hex(16)  # Generate a random secret key

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

def parse_whatsapp_chat(content):
    messages = []
    pattern = r'\[(\d{1,2}/\d{1,2}/\d{4}), (\d{1,2}:\d{2}:\d{2})\] ([^:]+): (.+)'
    
    for line in content.split('\n'):
        match = re.match(pattern, line)
        if match:
            date_str, time_str, sender, message = match.groups()
            
            # Skip system messages and media
            if any(skip in message for skip in ['<המדיה לא נכללה>', 'הוסיף/ה', 'יצר/ה את הקבוצה', 'התמונה הושמטה', 'השמע הושמט', 'סטיקר הושמט', 'הודעה זו נמחקה', 'Messages and calls are end-to-end encrypted']):
                continue
                
            try:
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
        },
        'activity_trends': {
            'daily': defaultdict(int),
            'weekly': defaultdict(int),
            'monthly': defaultdict(int)
        },
        'engagement_metrics': {
            'most_active_days': [],
            'least_active_days': [],
            'peak_hours': [],
            'quiet_hours': []
        },
        'user_metrics': {
            'top_contributors': [],
            'response_patterns': defaultdict(float),
            'activity_consistency': defaultdict(float)
        },
        'conversation_metrics': {
            'avg_response_time': 0,
            'conversation_starters': [],
            'conversation_length_distribution': defaultdict(int)
        }
    }
    
    total_length = 0
    prev_msg_time = None
    conversation_messages = []
    daily_messages = defaultdict(list)
    
    for msg in messages:
        stats['messages_per_sender'][msg['sender']] += 1
        stats['messages_per_hour'][msg['date'].hour] += 1
        stats['messages_per_day'][msg['date'].strftime('%Y-%m-%d')] += 1
        total_length += len(msg['message'])
        
        stats['activity_trends']['daily'][msg['date'].strftime('%Y-%m-%d')] += 1
        stats['activity_trends']['weekly'][msg['date'].strftime('%Y-%W')] += 1
        stats['activity_trends']['monthly'][msg['date'].strftime('%Y-%m')] += 1
        
        if prev_msg_time:
            time_diff = (msg['date'] - prev_msg_time).total_seconds()
            if time_diff < 3600:
                conversation_messages.append(msg)
                stats['conversation_metrics']['avg_response_time'] += time_diff
            else:
                if len(conversation_messages) > 1:
                    stats['conversation_metrics']['conversation_length_distribution'][len(conversation_messages)] += 1
                conversation_messages = [msg]
        
        prev_msg_time = msg['date']
        daily_messages[msg['date'].strftime('%Y-%m-%d')].append(msg)
    
    daily_activity = [(day, len(msgs)) for day, msgs in daily_messages.items()]
    daily_activity.sort(key=lambda x: x[1], reverse=True)
    stats['engagement_metrics']['most_active_days'] = daily_activity[:5]
    stats['engagement_metrics']['least_active_days'] = daily_activity[-5:]
    
    hourly_activity = [(hour, count) for hour, count in stats['messages_per_hour'].items()]
    hourly_activity.sort(key=lambda x: x[1], reverse=True)
    stats['engagement_metrics']['peak_hours'] = hourly_activity[:3]
    stats['engagement_metrics']['quiet_hours'] = hourly_activity[-3:]
    
    sender_activity = [(sender, count) for sender, count in stats['messages_per_sender'].items()]
    sender_activity.sort(key=lambda x: x[1], reverse=True)
    stats['user_metrics']['top_contributors'] = sender_activity[:5]
    
    total_conversations = sum(stats['conversation_metrics']['conversation_length_distribution'].values())
    if total_conversations > 0:
        stats['conversation_metrics']['avg_response_time'] /= total_conversations
    
    stats['messages_per_sender'] = dict(stats['messages_per_sender'])
    stats['messages_per_hour'] = dict(stats['messages_per_hour'])
    stats['messages_per_day'] = dict(stats['messages_per_day'])
    stats['activity_trends']['daily'] = dict(stats['activity_trends']['daily'])
    stats['activity_trends']['weekly'] = dict(stats['activity_trends']['weekly'])
    stats['activity_trends']['monthly'] = dict(stats['activity_trends']['monthly'])
    stats['conversation_metrics']['conversation_length_distribution'] = dict(stats['conversation_metrics']['conversation_length_distribution'])
    
    stats['most_active_hour'] = max(stats['messages_per_hour'].items(), key=lambda x: x[1])[0]
    
    stats['average_message_length'] = round(total_length / len(messages), 1)
    
    return stats

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    if path == '':
        return render_template('index.html')
    elif path == 'dashboard':
        return render_template('dashboard.html')
    elif path == 'help':
        return render_template('help.html')
    else:
        return render_template('index.html')

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
                # Find all text files in the zip (including in subdirectories)
                txt_files = []
                for f in zip_ref.namelist():
                    if f.endswith('.txt') and not f.startswith('__MACOSX'):
                        txt_files.append(f)
                
                if not txt_files:
                    return jsonify({'error': 'לא נמצא קובץ טקסט בקובץ ה-ZIP'}), 400
                
                # Try to read the first .txt file with different encodings
                content = None
                encodings = ['utf-8', 'utf-16', 'iso-8859-8', 'cp1255', 'windows-1255', 'mac-hebrew']
                
                for encoding in encodings:
                    try:
                        with zip_ref.open(txt_files[0]) as txt_file:
                            content = txt_file.read().decode(encoding)
                            break
                    except UnicodeDecodeError:
                        continue
                
                if content is None:
                    return jsonify({'error': 'לא ניתן לקרוא את הקובץ. אנא וודא שהקובץ בקידוד תקין'}), 400
        else:
            # Try to read the txt file with different encodings
            content = None
            encodings = ['utf-8', 'utf-16', 'iso-8859-8', 'cp1255', 'windows-1255', 'mac-hebrew']
            
            for encoding in encodings:
                try:
                    content = file.read().decode(encoding)
                    break
                except UnicodeDecodeError:
                    file.seek(0)  # Reset file pointer for next attempt
                    continue
            
            if content is None:
                return jsonify({'error': 'לא ניתן לקרוא את הקובץ. אנא וודא שהקובץ בקידוד תקין'}), 400
        
        messages = parse_whatsapp_chat(content)
        stats = calculate_statistics(messages)
        
        # Store only essential stats in session to reduce size
        session['chat_stats'] = {
            'total_messages': stats['total_messages'],
            'unique_senders': stats['unique_senders'],
            'average_message_length': stats['average_message_length'],
            'messages_per_hour': stats['messages_per_hour'],
            'messages_per_sender': dict(list(stats['messages_per_sender'].items())[:10]),  # Only top 10 senders
            'messages_per_day': dict(sorted(stats['messages_per_day'].items())[-30:])  # Only last 30 days
        }
        
        return jsonify({
            'success': True,
            'redirect': '/dashboard'
        })
        
    except zipfile.BadZipFile:
        return jsonify({'error': 'קובץ ZIP לא תקין'}), 400
    except zipfile.LargeZipFile:
        return jsonify({'error': 'קובץ ZIP גדול מדי'}), 400
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': 'אירעה שגיאה בעיבוד הקובץ. אנא נסה שוב'}), 400

@app.route('/dashboard')
def dashboard():
    stats = session.get('chat_stats')
    if not stats:
        return redirect('/')
    return render_template('dashboard.html', stats=stats)

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000) 