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
import tempfile
import pickle

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
    
    # חישוב התאריך לפני שנה
    one_year_ago = datetime.now().replace(year=datetime.now().year - 1)
    
    for line in content.split('\n'):
        match = re.match(pattern, line)
        if match:
            date_str, time_str, sender, message = match.groups()
            
            # Skip system messages and media
            if any(skip in message for skip in ['<המדיה לא נכללה>', 'הוסיף/ה', 'יצר/ה את הקבוצה', 'התמונה הושמטה', 'השמע הושמט', 'סטיקר הושמט', 'הודעה זו נמחקה', 'Messages and calls are end-to-end encrypted']):
                continue
                
            try:
                date_time = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M:%S")
                
                # סינון רק הודעות משנה אחרונה
                if date_time >= one_year_ago:
                    messages.append({
                        'date': date_time,
                        'sender': sender.strip(),
                        'message': message.strip()
                    })
            except ValueError:
                continue
    
    if not messages:
        raise ValueError("לא נמצאו הודעות תקינות בקובץ משנה אחרונה")
    
    return messages

def calculate_statistics(messages):
    stats = {
        'total_messages': len(messages),
        'messages_per_sender': defaultdict(int),
        'messages_per_hour': defaultdict(int),
        'messages_per_day': defaultdict(int),
        'most_active_hour': 0,
        'most_active_day': '',
        'most_messages_in_day': 0,
        'most_messages_in_hour': 0,
        'date_range': {
            'start': min(msg['date'] for msg in messages).strftime('%d/%m/%Y'),
            'end': max(msg['date'] for msg in messages).strftime('%d/%m/%Y')
        },
        'keywords': defaultdict(int),
        'avg_message_length': defaultdict(list),
        'messages_per_weekday': defaultdict(int),
        'message_length_distribution': {
            'short': 0,    # 1-10 תווים
            'medium': 0,   # 11-50 תווים
            'long': 0      # מעל 50 תווים
        }
    }
    
    # חישוב ימים ייחודיים
    unique_days = len(set(msg['date'].date() for msg in messages))
    stats['avg_messages_per_day'] = round(stats['total_messages'] / unique_days, 1) if unique_days > 0 else 0
    
    for msg in messages:
        stats['messages_per_sender'][msg['sender']] += 1
        stats['messages_per_hour'][msg['date'].hour] += 1
        day_str = msg['date'].strftime('%d/%m/%Y')
        stats['messages_per_day'][day_str] += 1
        
        # חישוב מילות מפתח
        words = msg['message'].split()
        for word in words:
            if len(word) > 2:  # התעלם ממילים קצרות
                stats['keywords'][word] += 1
        
        # חישוב אורך הודעה ממוצע
        msg_length = len(msg['message'])
        stats['avg_message_length'][msg['sender']].append(msg_length)
        
        # חישוב התפלגות אורך ההודעות
        if msg_length <= 10:
            stats['message_length_distribution']['short'] += 1
        elif msg_length <= 50:
            stats['message_length_distribution']['medium'] += 1
        else:
            stats['message_length_distribution']['long'] += 1
        
        # חישוב הודעות לפי יום בשבוע
        weekday = msg['date'].strftime('%A')  # שם היום באנגלית
        stats['messages_per_weekday'][weekday] += 1
        
        # מציאת היום עם הכי הרבה הודעות
        if stats['messages_per_day'][day_str] > stats['most_messages_in_day']:
            stats['most_messages_in_day'] = stats['messages_per_day'][day_str]
            stats['most_active_day'] = day_str
        
        # מציאת השעה עם הכי הרבה הודעות
        if stats['messages_per_hour'][msg['date'].hour] > stats['most_messages_in_hour']:
            stats['most_messages_in_hour'] = stats['messages_per_hour'][msg['date'].hour]
            stats['most_active_hour'] = msg['date'].hour
    
    # חישוב ממוצע אורך הודעות לכל משתתף
    stats['avg_message_length'] = {
        sender: round(sum(lengths) / len(lengths), 1)
        for sender, lengths in stats['avg_message_length'].items()
    }
    
    # מיון מילות המפתח לפי תדירות
    stats['keywords'] = dict(sorted(stats['keywords'].items(), key=lambda x: x[1], reverse=True)[:20])
    
    # מיון המשתמשים לפי כמות ההודעות מהגדול לקטן
    sorted_senders = dict(sorted(stats['messages_per_sender'].items(), key=lambda x: x[1], reverse=True))
    stats['messages_per_sender'] = sorted_senders
    
    # חישוב אחוז ההודעות של המשתמש הפעיל ביותר
    if sorted_senders and stats['total_messages'] > 0:
        most_active_user = next(iter(sorted_senders.items()))
        stats['most_active_user'] = {
            'name': most_active_user[0],
            'messages': most_active_user[1],
            'percentage': round((most_active_user[1] / stats['total_messages']) * 100, 1)
        }
    
    return stats

def filter_messages_by_date_range(messages, start_date=None, end_date=None):
    if not start_date and not end_date:
        return messages
    
    filtered_messages = []
    for msg in messages:
        msg_date = msg['date']
        if start_date and msg_date < start_date:
            continue
        if end_date and msg_date > end_date:
            continue
        filtered_messages.append(msg)
    
    return filtered_messages

def serialize_message(msg):
    return {
        'date': msg['date'].strftime('%Y-%m-%d %H:%M:%S'),
        'sender': msg['sender'],
        'message': msg['message']
    }

def deserialize_message(msg):
    return {
        'date': datetime.strptime(msg['date'], '%Y-%m-%d %H:%M:%S'),
        'sender': msg['sender'],
        'message': msg['message']
    }

def save_messages_to_temp(messages):
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pkl') as temp_file:
        pickle.dump(messages, temp_file)
        return temp_file.name

def load_messages_from_temp(filename):
    try:
        with open(filename, 'rb') as temp_file:
            return pickle.load(temp_file)
    except:
        return None

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
        
        # Save messages to temp file and store filename in session
        temp_filename = save_messages_to_temp(messages)
        session['messages_file'] = temp_filename
        
        # Calculate initial statistics (last year by default)
        one_year_ago = datetime.now().replace(year=datetime.now().year - 1)
        filtered_messages = filter_messages_by_date_range(messages, start_date=one_year_ago)
        stats = calculate_statistics(filtered_messages)
        
        # Store only essential stats in session
        session['chat_stats'] = {
            'total_messages': stats['total_messages'],
            'avg_messages_per_day': stats['avg_messages_per_day'],
            'most_active_day': stats['most_active_day'],
            'most_messages_in_day': stats['most_messages_in_day'],
            'most_active_hour': stats['most_active_hour'],
            'most_messages_in_hour': stats['most_messages_in_hour'],
            'most_active_user': stats['most_active_user'],
            'messages_per_hour': dict(list(stats['messages_per_hour'].items())),
            'messages_per_sender': dict(list(stats['messages_per_sender'].items())),
            'messages_per_day': dict(list(stats['messages_per_day'].items())[-30:]),
            'date_range': stats['date_range'],
            'keywords': stats['keywords'],
            'avg_message_length': stats['avg_message_length'],
            'messages_per_weekday': stats['messages_per_weekday'],
            'message_length_distribution': stats['message_length_distribution']
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

@app.route('/filter_stats', methods=['POST'])
def filter_stats():
    try:
        data = request.get_json()
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d') if data.get('start_date') else None
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d') if data.get('end_date') else None
        
        # Load messages from temp file
        messages_file = session.get('messages_file')
        if not messages_file:
            return jsonify({'error': 'לא נמצאו הודעות לניתוח'}), 400
            
        messages = load_messages_from_temp(messages_file)
        if not messages:
            return jsonify({'error': 'לא ניתן לטעון את ההודעות'}), 400
        
        # Filter messages by date range
        filtered_messages = filter_messages_by_date_range(messages, start_date, end_date)
        
        if not filtered_messages:
            return jsonify({'error': 'לא נמצאו הודעות בתקופה הנבחרת'}), 400
        
        # Calculate new statistics
        stats = calculate_statistics(filtered_messages)
        
        # Store only essential stats in session
        session['chat_stats'] = {
            'total_messages': stats['total_messages'],
            'avg_messages_per_day': stats['avg_messages_per_day'],
            'most_active_day': stats['most_active_day'],
            'most_messages_in_day': stats['most_messages_in_day'],
            'most_active_hour': stats['most_active_hour'],
            'most_messages_in_hour': stats['most_messages_in_hour'],
            'most_active_user': stats['most_active_user'],
            'messages_per_hour': dict(list(stats['messages_per_hour'].items())),
            'messages_per_sender': dict(list(stats['messages_per_sender'].items())),
            'messages_per_day': dict(list(stats['messages_per_day'].items())[-30:]),
            'date_range': stats['date_range'],
            'keywords': stats['keywords'],
            'avg_message_length': stats['avg_message_length'],
            'messages_per_weekday': stats['messages_per_weekday'],
            'message_length_distribution': stats['message_length_distribution']
        }
        
        return jsonify({
            'success': True,
            'stats': session['chat_stats']
        })
        
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': 'אירעה שגיאה בעיבוד הנתונים'}), 400

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 