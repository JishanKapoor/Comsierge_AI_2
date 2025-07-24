from flask import render_template, session, flash, redirect, url_for, request, jsonify
from db import store
from bson.objectid import ObjectId
from datetime import datetime
import pytz
import re
from deep_translator import GoogleTranslator
from langdetect import detect, DetectorFactory
import logging

# Configure logging
logging.getLogger('pymongo').setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# Ensure consistent language detection
DetectorFactory.seed = 0

phone_pattern = re.compile(r'^\+\d{10,15}$')

# Map language names to language codes for translation
ALLOWED_LANGUAGES = [
    'English', 'Spanish', 'French', 'German', 'Italian',
    'Portuguese', 'Russian', 'Chinese', 'Japanese', 'Korean'
]

LANGUAGE_CODE_MAP = {
    'English': 'en',
    'Spanish': 'es',
    'French': 'fr',
    'German': 'de',
    'Italian': 'it',
    'Portuguese': 'pt',
    'Russian': 'ru',
    'Chinese': 'zh-CN',  # Fixed from 'zh-cn' to 'zh-CN'
    'Japanese': 'ja',
    'Korean': 'ko'
}

def detect_language(text):
    """Detect the language of the given text."""
    try:
        lang_code = detect(text)
        reverse_map = {v: k for k, v in LANGUAGE_CODE_MAP.items()}
        return reverse_map.get(lang_code, 'English')  # Default to English
    except Exception as e:
        logger.error(f"Language detection failed: {str(e)}")
        return 'English'  # Fallback to English

def translate_message(text, source_lang, target_lang):
    """Translate text from source_lang to target_lang if different."""
    if not text or source_lang == target_lang:
        return text  # No translation needed
    try:
        source_code = LANGUAGE_CODE_MAP.get(source_lang, 'auto')
        target_code = LANGUAGE_CODE_MAP.get(target_lang, 'en')
        translated = GoogleTranslator(source=source_code, target=target_code).translate(text)
        return translated if translated else text
    except Exception as e:
        logger.error(f"Translation failed from {source_lang} to {target_lang}: {str(e)}")
        return text  # Return original text on failure

def format_sending_mode(mode_dict):
    mode_map = {
        'high_medium': 'High & Medium Priority',
        'high': 'High Priority Only',
        'all': 'All Messages',
        'dnd': 'Do Not Disturb (No Messages)'
    }
    mode_str = mode_map.get(mode_dict['mode'], 'Unknown')
    if mode_dict['duration'] == 'until_stopped':
        return f"{mode_str} until stopped"
    elif isinstance(mode_dict['duration'], dict):
        start = mode_dict['duration']['start'].strftime('%Y-%m-%d %H:%M UTC')
        end = mode_dict['duration']['end'].strftime('%Y-%m-%d %H:%M UTC')
        return f"{mode_str} from {start} to {end}"
    return 'Unknown'

def get_effective_sending_mode(user):
    now = datetime.now(pytz.UTC)
    sending_mode = user.get('sending_mode', {'mode': 'high_medium', 'duration': 'until_stopped'})

    if isinstance(sending_mode['duration'], dict):
        start = sending_mode['duration'].get('start')
        end = sending_mode['duration'].get('end')
        if start:
            start = start.replace(tzinfo=pytz.UTC)  # Make aware
        if end:
            end = end.replace(tzinfo=pytz.UTC)  # Make aware

        if 'pending' in sending_mode['duration'] and sending_mode['duration']['pending'] and start and now < start:
            # Pending schedule: Use default/previous mode
            return {'mode': 'high_medium', 'duration': 'until_stopped'}

        if end and now > end:
            # Ended: Reset to default and update DB
            default_mode = {'mode': 'high_medium', 'duration': 'until_stopped'}
            store.db.users.update_one({'_id': user['_id']}, {'$set': {'sending_mode': default_mode}})
            return default_mode

        # Active schedule: Use the scheduled mode
        return sending_mode

    # No schedule or until_stopped: Use current
    return sending_mode

def settings():
    username = session.get('username')
    user = store.db.users.find_one({'username': username})
    if not user:
        flash("User not found.", "error")
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    phone = None
    if selected_phone_id:
        phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
        phone = phone_doc['number'] if phone_doc else None

    # Compute effective mode (this will reset if schedule has ended)
    current_sending_mode_dict = get_effective_sending_mode(user)

    # Refresh user after potential update
    user = store.db.users.find_one({'username': username})
    sending_mode = user.get('sending_mode', {'mode': 'high_medium', 'duration': 'until_stopped'})

    # Determine upcoming if pending
    upcoming_sending_mode_dict = None
    if isinstance(sending_mode['duration'], dict) and sending_mode['duration'].get('pending'):
        upcoming_sending_mode_dict = sending_mode

    # Format strings for template
    current_sending_mode = format_sending_mode(current_sending_mode_dict)
    upcoming_sending_mode = format_sending_mode(upcoming_sending_mode_dict) if upcoming_sending_mode_dict else 'None'

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_personal_phone':
            personal_phone = request.form.get('phone_number')
            if not personal_phone:
                return jsonify({'success': False, 'error': 'Phone number is required'})
            if not phone_pattern.match(personal_phone):
                return jsonify({'success': False, 'error': 'Invalid phone number format'})
            store.db.users.update_one({'_id': user['_id']}, {'$set': {'personal_phone': personal_phone}})
            if selected_phone_id:
                existing_rule = store.db.forwarding_rules.find_one({
                    'phone_id': ObjectId(selected_phone_id),
                    'from_number': None,
                    'start_time': None,
                    'end_time': None,
                    'status': 'active'
                })
                if existing_rule:
                    store.db.forwarding_rules.update_one(
                        {'_id': existing_rule['_id']},
                        {'$set': {'forward_to': personal_phone}}
                    )
                else:
                    rule_data = {
                        'phone_id': ObjectId(selected_phone_id),
                        'from_number': None,
                        'forward_to': personal_phone,
                        'status': 'active',
                        'created_at': datetime.now(pytz.UTC),
                        'start_time': None,
                        'end_time': None
                    }
                    store.db.forwarding_rules.insert_one(rule_data)
            return jsonify({'success': True})
        elif action == 'update_sending_mode':
            mode = request.form.get('mode')
            duration = request.form.get('duration')
            if duration == 'schedule':
                try:
                    start_date = request.form.get('start_date')
                    start_time = request.form.get('start_time')
                    end_date = request.form.get('end_date')
                    end_time = request.form.get('end_time')
                    start_dt = pytz.UTC.localize(datetime.strptime(f"{start_date} {start_time}", '%Y-%m-%d %H:%M'))
                    end_dt = pytz.UTC.localize(datetime.strptime(f"{end_date} {end_time}", '%Y-%m-%d %H:%M'))
                    if start_dt >= end_dt:
                        return jsonify({'success': False, 'error': 'Invalid time range'})
                    now = datetime.now(pytz.UTC)
                    if start_dt > now:
                        sending_mode = {'mode': mode, 'duration': {'start': start_dt, 'end': end_dt, 'pending': True}}
                    else:
                        sending_mode = {'mode': mode, 'duration': {'start': start_dt, 'end': end_dt}}
                    store.db.users.update_one({'_id': user['_id']}, {'$set': {'sending_mode': sending_mode}})
                except Exception as e:
                    return jsonify({'success': False, 'error': 'Invalid date/time'})
            else:
                sending_mode = {'mode': mode, 'duration': 'until_stopped'}
                store.db.users.update_one({'_id': user['_id']}, {'$set': {'sending_mode': sending_mode}})

            # Refresh and compute for response
            user = store.db.users.find_one({'_id': user['_id']})
            current_sending_mode_dict = get_effective_sending_mode(user)
            sending_mode = user['sending_mode']
            upcoming_sending_mode_dict = sending_mode if isinstance(sending_mode['duration'], dict) and sending_mode['duration'].get('pending') else None
            current_str = format_sending_mode(current_sending_mode_dict)
            upcoming_str = format_sending_mode(upcoming_sending_mode_dict) if upcoming_sending_mode_dict else 'None'
            return jsonify({'success': True, 'current_mode': current_str, 'upcoming_mode': upcoming_str})
        elif action == 'cancel_upcoming':
            default_mode = {'mode': 'high_medium', 'duration': 'until_stopped'}
            store.db.users.update_one({'_id': user['_id']}, {'$set': {'sending_mode': default_mode}})
            # Refresh and compute
            user = store.db.users.find_one({'_id': user['_id']})
            current_sending_mode_dict = get_effective_sending_mode(user)
            sending_mode = user['sending_mode']
            upcoming_sending_mode_dict = None
            current_str = format_sending_mode(current_sending_mode_dict)
            upcoming_str = 'None'
            return jsonify({'success': True, 'current_mode': current_str, 'upcoming_mode': upcoming_str})
        elif action == 'update_receive_language':
            lang = request.form.get('language')
            if lang in ALLOWED_LANGUAGES:
                store.db.users.update_one({'_id': user['_id']}, {'$set': {'receive_language': lang}})
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Invalid language'})
        elif action == 'update_send_language':
            lang = request.form.get('language')
            if lang in ALLOWED_LANGUAGES:
                store.db.users.update_one({'_id': user['_id']}, {'$set': {'send_language': lang}})
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Invalid language'})
        elif action == 'add_priority_from':
            input_value = request.form.get('value').strip()
            if not input_value:
                return jsonify({'success': False, 'error': 'Value required'})
            phone_number = None
            if phone_pattern.match(input_value):
                phone_number = input_value
            else:
                contact = store.db.contacts.find_one({'phone_id': ObjectId(selected_phone_id), 'alias': {'$regex': f'^{re.escape(input_value)}$', '$options': 'i'}})
                if contact:
                    phone_number = contact['phone_number']
                else:
                    return jsonify({'success': False, 'error': 'Contact not found'})
            if phone_number:
                priority_from = user.get('priority_from', [])
                if phone_number in priority_from:
                    return jsonify({'success': False, 'error': 'Already added'})
                store.db.users.update_one({'_id': user['_id']}, {'$push': {'priority_from': phone_number}})
                return jsonify({'success': True})
        elif action == 'remove_priority_from':
            value = request.form.get('value')
            store.db.users.update_one({'_id': user['_id']}, {'$pull': {'priority_from': value}})
            return jsonify({'success': True})
        elif action == 'add_priority_keyword':
            kw = request.form.get('value').strip().lower()
            if kw:
                priority_keywords = [k.lower() for k in user.get('priority_keywords', [])]
                if kw in priority_keywords:
                    return jsonify({'success': False, 'error': 'Duplicate keyword'})
                store.db.users.update_one({'_id': user['_id']}, {'$push': {'priority_keywords': kw}})
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Keyword required'})
        elif action == 'remove_priority_keyword':
            kw = request.form.get('value').lower()
            store.db.users.update_one({'_id': user['_id']}, {'$pull': {'priority_keywords': kw}})
            return jsonify({'success': True})
        elif action == 'add_spam_ignore_from':
            input_value = request.form.get('value').strip()
            if not input_value:
                return jsonify({'success': False, 'error': 'Value required'})
            phone_number = None
            if phone_pattern.match(input_value):
                phone_number = input_value
            else:
                contact = store.db.contacts.find_one({'phone_id': ObjectId(selected_phone_id), 'alias': {'$regex': f'^{re.escape(input_value)}$', '$options': 'i'}})
                if contact:
                    phone_number = contact['phone_number']
                else:
                    return jsonify({'success': False, 'error': 'Contact not found'})
            if phone_number:
                spam_ignore_from = user.get('spam_ignore_from', [])
                if phone_number in spam_ignore_from:
                    return jsonify({'success': False, 'error': 'Already added'})
                store.db.users.update_one({'_id': user['_id']}, {'$push': {'spam_ignore_from': phone_number}})
                return jsonify({'success': True})
        elif action == 'remove_spam_ignore_from':
            value = request.form.get('value')
            store.db.users.update_one({'_id': user['_id']}, {'$pull': {'spam_ignore_from': value}})
            return jsonify({'success': True})
        elif action == 'add_spam_keyword':
            kw = request.form.get('value').strip().lower()
            if kw:
                spam_keywords = [k.lower() for k in user.get('spam_keywords', [])]
                if kw in spam_keywords:
                    return jsonify({'success': False, 'error': 'Duplicate keyword'})
                store.db.users.update_one({'_id': user['_id']}, {'$push': {'spam_keywords': kw}})
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Keyword required'})
        elif action == 'remove_spam_keyword':
            kw = request.form.get('value').lower()
            store.db.users.update_one({'_id': user['_id']}, {'$pull': {'spam_keywords': kw}})
            return jsonify({'success': True})

    # GET or after POST redirect
    user = store.db.users.find_one({'username': username})  # Refresh user
    contacts = list(store.db.contacts.find({'phone_id': ObjectId(selected_phone_id)}).sort('alias', 1)) if selected_phone_id else []
    priority_from = user.get('priority_from', [])
    priority_from_displays = [{'value': pf, 'display': next((c['alias'] for c in contacts if c['phone_number'] == pf), pf)} for pf in priority_from]
    priority_keywords = user.get('priority_keywords', [])
    spam_ignore_from = user.get('spam_ignore_from', [])
    spam_ignore_displays = [{'value': sf, 'display': next((c['alias'] for c in contacts if c['phone_number'] == sf), sf)} for sf in spam_ignore_from]
    spam_keywords = user.get('spam_keywords', [])
    personal_phone = user.get('personal_phone', '')
    receive_language = user.get('receive_language', 'English')
    send_language = user.get('send_language', 'English')

    return render_template(
        'settings.html',
        user_name=user.get('name'),
        phone_number=phone,
        personal_phone=personal_phone,
        priority_from_displays=priority_from_displays,
        priority_keywords=priority_keywords,
        spam_ignore_displays=spam_ignore_displays,
        spam_keywords=spam_keywords,
        sending_mode=sending_mode,
        current_sending_mode=current_sending_mode,
        upcoming_sending_mode=upcoming_sending_mode,
        receive_language=receive_language,
        send_language=send_language,
        active_section='settings'
    )