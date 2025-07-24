from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_bcrypt import Bcrypt
from flask import jsonify
from flask import make_response
from functools import wraps
from db import users_collection, store
from bson.objectid import ObjectId
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from datetime import datetime
import pytz
from routes import sms_forwarding, add_forwarding_rule, stop_forwarding_rule, sms_scheduling, schedule_message, \
    cancel_message, add_contact, delete_chat, scheduler
from admin import unassign_phone_number, delete_user
import logging
import re
from flask_socketio import emit, join_room
from extensions import socketio  # Add this
import json
from ai import classify_priority, allow_held_message, delete_held_messages, held_messages,classify_other, classify_high_type
import threading
from settings import detect_language, translate_message # Add this import
from settings import settings, get_effective_sending_mode # Add this import
import upcoming
import ai
from ai import HIGH_TYPES
import random
# Add this import
# Configure logging
logging.getLogger('pymongo').setLevel(logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = "supersecretkey"
socketio.init_app(app, cors_allowed_origins="*", async_mode="eventlet")
bcrypt = Bcrypt(app)

@app.context_processor
def inject_user_helpers():
    def get_phone_number():
        selected_phone_id = session.get('selected_phone_id')
        if selected_phone_id:
            phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
            return phone_doc['number'] if phone_doc else None
        return None

    return {
        "is_admin": lambda: session.get('is_admin', False),
        "get_phone_number": get_phone_number
    }


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash("Please log in to access this page.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in') or not session.get('is_admin'):
            flash("Admin access required.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/investors')
def investor():
    return render_template('investors.html')


@socketio.on('join_room')
def handle_join_room(data):
    room = data
    join_room(room)
    logger.debug(f"Client joined room: {room}")


@app.route('/login', methods=['GET', 'POST'])
def login():
    # Clear session and prevent caching on GET request
    if request.method == 'GET':
        session.clear()

    # If user is already logged in, redirect accordingly
    if session.get('logged_in'):
        if session.get('is_admin', False):
            return redirect(url_for('admin_panel'))
        elif not session.get('selected_phone_id'):
            return redirect(url_for('select_phone'))
        else:
            return redirect(url_for('dashboard'))

    # Handle login submission
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('Username and password are required.', 'error')
            response = make_response(render_template('login.html', active_section='login'))
        else:
            user = users_collection.find_one({'username': username})
            if user and bcrypt.check_password_hash(user['password'], password):
                session['logged_in'] = True
                session['user_id'] = str(user['_id'])
                session['username'] = username
                session['name'] = user.get('name')
                session['is_admin'] = user.get('is_admin', False)
                selected_phone_id = user.get('selected_phone_id')
                session['selected_phone_id'] = str(selected_phone_id) if selected_phone_id else None

                if session['is_admin']:
                    return redirect(url_for('admin_panel'))
                elif not session.get('selected_phone_id'):
                    return redirect(url_for('select_phone'))
                else:
                    return redirect(url_for('dashboard'))

            flash('Invalid credentials', 'error')

        # Fallthrough in case of login error
        response = make_response(render_template('login.html', active_section='login'))
    else:
        # GET request — return login page
        response = make_response(render_template('login.html', active_section='login'))

    # Set no-cache headers to prevent forward/back button reuse
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        name = request.form.get('name')
        phone = request.form.get('phone')
        password = request.form.get('password')

        if not username or not name or not phone or not password:
            flash('All fields are required.', 'error')
            return render_template('signup.html', active_section='signup')

        phone_pattern = re.compile(r'^\+\d{10,15}$')
        if not phone_pattern.match(phone):
            flash('Invalid phone number format.', 'error')
            return render_template('signup.html', active_section='signup')

        if users_collection.find_one({'username': username}):
            flash('Username already exists.', 'error')
            return render_template('signup.html', active_section='signup')

        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        users_collection.insert_one({
            'username': username,
            'name': name,
            'personal_phone': phone,
            'password': hashed_pw,
            'is_admin': False,
            'selected_phone_id': None,
            'receive_language': 'English',
            'send_language': 'English',
            'sending_mode': {'mode': 'high_medium', 'duration': 'until_stopped'}
        })

        flash('Account created successfully! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('signup.html', active_section='signup')

@app.route('/held_messages')
@login_required
def held_messages_route():
    return held_messages()

@app.route('/delete_held_messages', methods=['POST'])
@app.route('/delete_held_messages/<message_id>', methods=['POST'])
@login_required
def delete_held_messages_route(message_id=None):
    return delete_held_messages(message_id)

@app.route('/dashboard')
@login_required
def dashboard():
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

    unread_count = store.db.message_log.count_documents(
        {'direction': 'received', 'read': False, 'phone_id': ObjectId(selected_phone_id)} if selected_phone_id else {})
    spam_count = store.db.message_log.count_documents(
        {'direction': 'received', 'priority': 'low', 'phone_id': ObjectId(selected_phone_id)} if selected_phone_id else {})
    contact_count = store.db.contacts.count_documents(
        {'phone_id': ObjectId(selected_phone_id)} if selected_phone_id else {})
    active_rules = store.db.forwarding_rules.count_documents(
        {'phone_id': ObjectId(selected_phone_id), 'status': 'active'} if selected_phone_id else {})
    pending_messages = store.db.scheduled_messages.count_documents(
        {'phone_id': ObjectId(selected_phone_id), 'status': 'pending'} if selected_phone_id else {})
    active_conditions = active_rules + pending_messages
    high_priority_messages = list(store.db.message_log.find(
        {'phone_id': ObjectId(selected_phone_id), 'direction': 'received', 'priority': 'high'} if selected_phone_id else {}
    ).sort('timestamp', -1).limit(10))
    contacts = list(store.db.contacts.find({'phone_id': ObjectId(selected_phone_id)}).sort('alias', 1)) if selected_phone_id else []

    return render_template(
        'dashboard.html',
        user_name=user.get('name'),
        phone_number=phone,
        unread_count=unread_count,
        spam_count=spam_count,
        contact_count=contact_count,
        active_rules=active_rules,
        pending_messages=pending_messages,
        active_conditions=active_conditions,
        high_priority_messages=high_priority_messages,
        contacts=contacts,
        active_section='dashboard'
    )
@app.route('/inbox')
@login_required
def inbox():
    username = session.get('username')
    user = store.db.users.find_one({'username': username})

    if not user:
        flash("User not found.", "error")
        logger.error(f"User not found: {username}")
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    phone_number = None

    if selected_phone_id:
        phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
        phone_number = phone_doc['number'] if phone_doc else None
    else:
        logger.debug(f"No selected_phone_id for user {username}")

    if not selected_phone_id or not phone_number:
        flash("No phone number selected.", "error")
        logger.error(f"No phone number selected for user {username}, selected_phone_id: {selected_phone_id}")
        return redirect(url_for('select_phone'))

    receive_language = user.get('receive_language', 'English')

    # Fetch contacts (scoped to phone_id for user isolation)
    contacts = list(store.db.contacts.find({'phone_id': ObjectId(selected_phone_id)}).sort('alias', 1))
    logger.debug(f"Fetched {len(contacts)} contacts for phone_id: {selected_phone_id}")

    # Aggregate unique conversations (counterparts, scoped to phone_id)
    pipeline = [
        {'$match': {'phone_id': ObjectId(selected_phone_id), 'direction': {'$in': ['received', 'sent']}}},
        {'$project': {
            'counterpart': {
                '$cond': [
                    {'$eq': ['$direction', 'received']},
                    {'$ifNull': ['$from_number', '$sender']},
                    '$to_number'
                ]
            },
            'timestamp': '$timestamp',
            'body': '$body'
        }},
        {'$group': {
            '_id': '$counterpart',
            'last_timestamp': {'$max': '$timestamp'},
            'last_body': {'$last': '$body'},
            'count': {'$sum': 1}
        }},
        {'$sort': {'last_timestamp': -1}}
    ]
    aggregated_convs = list(store.db.message_log.aggregate(pipeline))
    logger.debug(f"Aggregated conversations: {len(aggregated_convs)} for phone_id: {selected_phone_id}")

    conversations = []
    for agg in aggregated_convs:
        counterpart = agg['_id']
        contact = next((c for c in contacts if c['phone_number'] == counterpart), None)
        unread = store.db.message_log.count_documents({
            'phone_id': ObjectId(selected_phone_id),
            'direction': 'received',
            'from_number': counterpart,
            'read': False
        })
        high_priority_unread = store.db.message_log.count_documents({
            'phone_id': ObjectId(selected_phone_id),
            'direction': 'received',
            'from_number': counterpart,
            'priority': 'high',
            'read': False
        }) > 0
        conversations.append({
            'counterpart': counterpart,
            'alias': contact['alias'] if contact else counterpart,
            'label': contact['label'] if contact else 'N/A',
            'is_contact': bool(contact),
            'count': agg['count'],
            'last_message': agg['last_body'],
            'last_timestamp': agg['last_timestamp'].isoformat() if agg['last_timestamp'] else None,
            'unread': unread,
            'highPriority': high_priority_unread
        })

    logger.debug(f"Processed conversations: {len(conversations)}")

    # Include contacts without messages
    for contact in contacts:
        if not any(c['counterpart'] == contact['phone_number'] for c in conversations):
            unread = store.db.message_log.count_documents({
                'phone_id': ObjectId(selected_phone_id),
                'direction': 'received',
                'from_number': contact['phone_number'],
                'read': False
            })
            high_priority_unread = store.db.message_log.count_documents({
                'phone_id': ObjectId(selected_phone_id),
                'direction': 'received',
                'from_number': contact['phone_number'],
                'priority': 'high',
                'read': False
            }) > 0
            conversations.append({
                'counterpart': contact['phone_number'],
                'alias': contact['alias'],
                'label': contact['label'] or 'N/A',
                'is_contact': True,
                'count': 0,
                'last_message': None,
                'last_timestamp': None,
                'unread': unread,
                'highPriority': high_priority_unread
            })

    # Fetch and group all relevant messages (received and sent, excluding forwarded, scoped to phone_id)
    messages_query = {
        'phone_id': ObjectId(selected_phone_id),
        'direction': {'$in': ['received', 'sent']}
    }
    all_messages = list(store.db.message_log.find(messages_query).sort('timestamp', 1))
    logger.debug(f"Retrieved {len(all_messages)} messages for phone_id: {selected_phone_id}")

    grouped_messages = {}
    for msg in all_messages:
        if msg['direction'] == 'received':
            counterpart = msg.get('from_number') or msg.get('sender')
        else:  # sent
            counterpart = msg.get('to_number')

        if counterpart not in grouped_messages:
            grouped_messages[counterpart] = []

        display_body = msg['body']
        if msg['direction'] == 'received':
            display_body = msg.get('body_translated', msg['body'])

        grouped_messages[counterpart].append({
            'id': str(msg['_id']),
            'direction': msg['direction'],
            'body': display_body,
            'timestamp': msg['timestamp'].isoformat(),
            'priority': msg.get('priority', 'medium'),
            'isAI': msg.get('isAI', False)
        })

    # Total unread (scoped to phone_id)
    total_unread = store.db.message_log.count_documents({
        'phone_id': ObjectId(selected_phone_id),
        'direction': 'received',
        'read': False
    })

    return render_template(
        'inbox.html',
        conversations_json=json.dumps(conversations),
        grouped_messages_json=json.dumps(grouped_messages),
        phone_number=phone_number,
        selected_phone_id=selected_phone_id,
        active_section='inbox',
        total_unread=total_unread
    )

# app.py updates (add these routes)

@app.route('/generate_summary/<phone>', methods=['GET'])
@login_required
def generate_summary(phone):
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    # Fetch last 10 messages for better context
    messages = list(store.db.message_log.find({
        'phone_id': ObjectId(selected_phone_id),
        '$or': [{'from_number': phone, 'direction': 'received'}, {'to_number': phone, 'direction': 'sent'}]
    }).sort('timestamp', -1).limit(10))

    history = []
    for msg in reversed(messages):
        role = "user" if msg['direction'] == 'received' else "assistant"
        history.append({"role": role, "content": msg['body']})

    try:
        summary = ai.summarize_conversation(history)
        return jsonify({'summary': summary}), 200
    except Exception as e:
        logger.error(f"Error generating summary: {str(e)}")
        return jsonify({'error': 'Failed to generate summary'}), 500

@app.route('/generate_suggestions/<phone>', methods=['GET'])
@login_required
def generate_suggestions(phone):
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    # Fetch last 5 messages for context (scoped to phone_id)
    messages = list(store.db.message_log.find({
        'phone_id': ObjectId(selected_phone_id),
        '$or': [{'from_number': phone, 'direction': 'received'}, {'to_number': phone, 'direction': 'sent'}]
    }).sort('timestamp', -1).limit(5))

    history = []
    for msg in reversed(messages):  # Reverse to chronological order
        role = "user" if msg['direction'] == 'received' else "assistant"
        history.append({"role": role, "content": msg['body']})

    try:
        suggestions = ai.generate_suggestions(history)
        return jsonify({'suggestions': suggestions}), 200
    except Exception as e:
        logger.error(f"Error generating suggestions: {str(e)}")
        return jsonify({'error': 'Failed to generate suggestions'}), 500

@app.route('/rewrite_message', methods=['POST'])
@login_required
def rewrite_message_route():
    data = request.get_json()
    message = data.get('message')
    if not message:
        return jsonify({'error': 'Message required'}), 400

    try:
        rewritten = ai.rewrite_message(message)
        return jsonify({'rewritten': rewritten}), 200
    except Exception as e:
        logger.error(f"Error rewriting message: {str(e)}")
        return jsonify({'error': 'Failed to rewrite message'}), 500

@app.route('/select_phone', methods=['GET', 'POST'])
@login_required
def select_phone():
    username = session.get('username')
    user = store.db.users.find_one({'username': username})
    if user.get('is_admin'):
        return redirect(url_for('admin_panel'))
    if user and user.get('selected_phone_id'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        phone_id = request.form.get('phone_number')
        if not phone_id:
            flash('Please select a phone number.', 'error')
        else:
            phone_doc = store.db.phone_numbers.find_one(
                {'_id': ObjectId(phone_id), 'active': True, 'user_username': None})
            if not phone_doc:
                flash('Invalid phone number selected.', 'error')
            else:
                store.db.users.update_one({'username': username}, {'$set': {'selected_phone_id': ObjectId(phone_id)}})
                store.db.phone_numbers.update_one({'_id': ObjectId(phone_id)}, {'$set': {'user_username': username}})
                session['selected_phone_id'] = str(phone_id)

                # Auto-create forwarding rule to personal phone if exists
                personal_phone = user.get('personal_phone')
                if personal_phone:
                    existing_rule = store.db.forwarding_rules.find_one({
                        'phone_id': ObjectId(phone_id),
                        'from_number': None,
                        'start_time': None,
                        'end_time': None,
                        'status': 'active'
                    })
                    if not existing_rule:
                        rule_data = {
                            'phone_id': ObjectId(phone_id),
                            'from_number': None,
                            'forward_to': personal_phone,
                            'status': 'active',
                            'created_at': datetime.now(pytz.UTC),
                            'start_time': None,
                            'end_time': None
                        }
                        store.db.forwarding_rules.insert_one(rule_data)

                flash('Phone number selected successfully.', 'success')
                return redirect(url_for('dashboard'))

    phone_numbers = list(store.db.phone_numbers.find({'user_username': None, 'active': True}))
    return render_template('select_phone.html', phone_numbers=phone_numbers, active_section='select-phone')

@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin_panel():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            number = request.form.get('number')
            account_sid = request.form.get('account_sid')
            auth_token = request.form.get('auth_token')
            if not number or not account_sid or not auth_token:
                flash('All fields (number, account SID, auth token) are required.', 'error')
            else:
                try:
                    client = Client(account_sid, auth_token)
                    phones = client.incoming_phone_numbers.list(phone_number=number)
                    if not phones:
                        flash('Phone number not found in the account.', 'error')
                    else:
                        twilio_sid = phones[0].sid
                        if store.db.phone_numbers.find_one({'sid': twilio_sid}):
                            flash('This phone number already exists.', 'error')
                        else:
                            store.db.phone_numbers.insert_one({
                                'number': number,
                                'account_sid': account_sid,
                                'auth_token': auth_token,
                                'sid': twilio_sid,
                                'active': True,
                                'user_username': None
                            })
                            flash('Phone number added successfully.', 'success')
                except TwilioRestException as e:
                    logger.error(f"Failed to verify Twilio phone number {number}: {str(e)}")
                    flash(f'Error adding phone number: {str(e)}', 'error')
        elif action == 'assign':
            user_id = request.form.get('user_id')
            number_id = request.form.get('number_id')
            if not user_id or not number_id:
                flash('User ID and phone number are required.', 'error')
            else:
                user = store.db.users.find_one({'_id': ObjectId(user_id)})
                phone = store.db.phone_numbers.find_one(
                    {'_id': ObjectId(number_id), 'active': True, 'user_username': None})
                if not user or not phone:
                    flash('Invalid user or phone number.', 'error')
                else:
                    store.db.users.update_one({'_id': ObjectId(user_id)},
                                              {'$set': {'selected_phone_id': ObjectId(number_id)}})
                    store.db.phone_numbers.update_one({'_id': ObjectId(number_id)},
                                                      {'$set': {'user_username': user['username']}})
                    flash('Phone number assigned successfully.', 'success')
        elif action == 'unassign':
            user_id = request.form.get('user_id')
            if not user_id:
                flash('User ID is required.', 'error')
            else:
                unassign_phone_number(user_id)
        elif action == 'delete_user':
            user_id = request.form.get('user_id')
            if not user_id:
                flash('User ID is required.', 'error')
            else:
                delete_user(user_id)
        return redirect(url_for('admin_panel'))

    # Prepare data for Twilio accounts tables
    phone_numbers = list(store.db.phone_numbers.find())
    twilio_accounts = {}
    for phone in phone_numbers:
        account_sid = phone['account_sid']
        if account_sid not in twilio_accounts:
            twilio_accounts[account_sid] = {
                'sid': account_sid,
                'numbers': []
            }
        twilio_accounts[account_sid]['numbers'].append(
            f"{phone['number']} {'(Inactive)' if not phone['active'] else ''}")

    twilio_accounts = list(twilio_accounts.values())

    available_twilio_accounts = {}
    for phone in phone_numbers:
        if phone['active'] and phone['user_username'] is None:
            account_sid = phone['account_sid']
            if account_sid not in available_twilio_accounts:
                available_twilio_accounts[account_sid] = {
                    'sid': account_sid,
                    'numbers': []
                }
            available_twilio_accounts[account_sid]['numbers'].append(phone['number'])

    available_twilio_accounts = list(available_twilio_accounts.values())

    user_list = list(users_collection.find({'is_admin': False}))
    for user in user_list:
        user['phone_number'] = None
        if user.get('selected_phone_id'):
            phone_doc = store.db.phone_numbers.find_one({'_id': user['selected_phone_id'], 'active': True})
            user['phone_number'] = phone_doc['number'] if phone_doc else None
        user['personal_phone'] = user.get('personal_phone', None)

    unassigned_numbers = list(store.db.phone_numbers.find({'user_username': None, 'active': True}))
    return render_template(
        'admin_panel.html',
        user_list=user_list,
        unassigned_numbers=unassigned_numbers,
        twilio_accounts=twilio_accounts,
        available_twilio_accounts=available_twilio_accounts,
        active_section='admin'
    )

@app.route('/delete_message/<message_id>', methods=['POST'])
@login_required
def delete_message(message_id):
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    try:
        result = store.db.message_log.delete_one({
            '_id': ObjectId(message_id),
            'phone_id': ObjectId(selected_phone_id)
        })
        if result.deleted_count > 0:
            socketio.emit('message_deleted', {'message_id': message_id}, room=f"user_{selected_phone_id}")
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': 'Message not found'}), 404
    except Exception as e:
        logger.error(f"Error deleting message {message_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/mark_read/<phone_number>', methods=['POST'])
@login_required
def mark_read(phone_number):
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    phone_number_pattern = re.compile(r'^\+\d{10,15}$')
    if not phone_number_pattern.match(phone_number):
        return jsonify({'error': 'Invalid phone number format'}), 400

    try:
        result = store.db.message_log.update_many({
            'phone_id': ObjectId(selected_phone_id),
            'direction': 'received',
            'from_number': phone_number,
            'read': False
        }, {'$set': {'read': True}})
        return jsonify({'success': True, 'updated': result.modified_count}), 200
    except Exception as e:
        logger.error(f"Error marking read for {phone_number}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/contacts', methods=['GET'])
@login_required
def contacts():
    return add_contact()

@app.route('/add_contact', methods=['POST'])
@login_required
def add_contact_route():
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    alias = request.form.get('alias')
    phone_number = request.form.get('phone_number')
    label = request.form.get('label')

    if not alias or not phone_number:
        return jsonify({'error': 'Alias and phone number are required'}), 400

    phone_number_pattern = re.compile(r'^\+\d{10,15}$')
    if not phone_number_pattern.match(phone_number):
        return jsonify({'error': 'Invalid phone number format. Use + followed by 10-15 digits.'}), 400

    phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
    if not phone_doc:
        return jsonify({'error': 'No active phone number found'}), 400

    if phone_number == phone_doc['number']:
        return jsonify({'error': 'Cannot add a contact with the same number as the selected phone'}), 400

    # Check for duplicate alias
    if store.db.contacts.find_one({'phone_id': ObjectId(selected_phone_id), 'alias': alias}):
        return jsonify({'error': 'Contact with this alias already exists'}), 400

    # Check for duplicate phone number
    if store.db.contacts.find_one({'phone_id': ObjectId(selected_phone_id), 'phone_number': phone_number}):
        return jsonify({'error': 'Contact with this phone number already exists'}), 400

    contact_data = {
        'phone_id': ObjectId(selected_phone_id),
        'alias': alias,
        'phone_number': phone_number,
        'label': label or 'None',
        'created_at': datetime.now(pytz.UTC)
    }

    try:
        store.db.contacts.insert_one(contact_data)
        # Emit real-time update for new contact
        socketio.emit('update_contact_label', {
            'phone_number': phone_number,
            'label': label or 'None',
            'alias': alias
        }, room=f"user_{selected_phone_id}")
        return jsonify({'success': True}), 200
    except Exception as e:
        logger.error(f"Error adding contact: {str(e)}", exc_info=True)
        return jsonify({'error': f'Error adding contact: {str(e)}'}), 500

@app.route('/delete_contact/<phone_number>', methods=['POST'])
@login_required
def delete_contact_route(phone_number):
    return delete_contact(phone_number)


@app.route('/sms_forwarding')
@login_required
def sms_forwarding_route():
    from_number = request.args.get('from_number', '')
    return sms_forwarding(from_number=from_number)

@app.route('/add_forwarding_rule', methods=['POST'])
def add_forwarding_rule_route():
    return add_forwarding_rule()


@app.route('/stop_forwarding_rule/<rule_id>', methods=['POST'])
def stop_forwarding_rule_route(rule_id):
    return stop_forwarding_rule(rule_id)

@app.route('/sms_scheduling')
@login_required
def sms_scheduling_route():
    to_number = request.args.get('to_number', '')
    return sms_scheduling(to_number=to_number)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_route():
    return settings()

@app.route('/schedule_message', methods=['POST'])
def schedule_message_route():
    return schedule_message()

@app.route('/cancel_message/<msg_id>', methods=['POST'])
def cancel_message_route(msg_id):
    return cancel_message(msg_id)


@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for('login'))

@app.route('/assign_label', methods=['POST'])
@login_required
def assign_label():
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return 'No phone number selected', 400

    if request.method == 'POST':
        phone_number = request.form.get('phone_number')
        label = request.form.get('label')
        alias = request.form.get('alias')

        if not phone_number or not label or not alias:
            return 'Missing data', 400

        phone_number_pattern = re.compile(r'^\+\d{10,15}$')
        if not phone_number_pattern.match(phone_number):
            return 'Invalid phone number format', 400

        contact = store.db.contacts.find_one({'phone_id': ObjectId(selected_phone_id), 'phone_number': phone_number})
        current_time = datetime.now(pytz.UTC)

        if contact:
            store.db.contacts.update_one(
                {'_id': contact['_id']},
                {'$set': {'label': label, 'alias': alias, 'updated_at': current_time}}
            )
        else:
            store.db.contacts.insert_one({
                'phone_id': ObjectId(selected_phone_id),
                'alias': alias,
                'phone_number': phone_number,
                'label': label,
                'created_at': current_time
            })

        # Emit real-time update for label change
        socketio.emit('update_contact_label', {
            'phone_number': phone_number,
            'label': label,
            'alias': alias
        }, room=f"user_{selected_phone_id}")

        return 'Success', 200

    return 'Invalid method', 405

@app.route('/delete_chat/<phone_number>', methods=['POST'])
@login_required
def delete_chat_route(phone_number):
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    phone_number_pattern = re.compile(r'^\+\d{10,15}$')
    if not phone_number_pattern.match(phone_number):
        return jsonify({'error': 'Invalid phone number format'}), 400

    # Delete all messages in the conversation
    delete_result = store.db.message_log.delete_many({
        'phone_id': ObjectId(selected_phone_id),
        '$or': [
            {'from_number': phone_number, 'direction': 'received'},
            {'to_number': phone_number, 'direction': 'sent'}
        ]
    })

    # Also delete the contact if it exists to prevent reappearance
    contact_delete_result = store.db.contacts.delete_one({
        'phone_id': ObjectId(selected_phone_id),
        'phone_number': phone_number
    })

    if delete_result.deleted_count > 0 or contact_delete_result.deleted_count > 0:
        # Emit socket event for chat deletion
        socketio.emit('chat_deleted', {'phone_number': phone_number}, room=f"user_{selected_phone_id}")
        # Emit contact_deleted if contact was deleted
        if contact_delete_result.deleted_count > 0:
            socketio.emit('contact_deleted', {'phone_number': phone_number}, room=f"user_{selected_phone_id}")
        return jsonify({'success': True}), 200
    else:
        return jsonify({'error': 'No chat found to delete'}), 404


@app.route('/upcoming')
@login_required
def upcoming_route():
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash("No phone number selected.", "error")
        return redirect(url_for('select_phone'))

    upcoming.cleanup_past_events()  # Clean up past events
    phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
    phone_number = phone_doc['number'] if phone_doc else None

    if not phone_number:
        flash("Invalid phone number.", "error")
        return redirect(url_for('select_phone'))

    events = upcoming.get_upcoming_events(selected_phone_id)
    contacts = list(store.db.contacts.find({'phone_id': ObjectId(selected_phone_id)}).sort('alias', 1))

    return render_template('upcoming.html', events=events, contacts=contacts, phone_number=phone_number, phone_id=selected_phone_id, active_section='upcoming')

@app.route('/twilio_webhook', methods=['POST'])
def twilio_webhook():
    incoming_msg = request.values.get('Body', '').strip()
    from_number = request.values.get('From', '').strip()
    to_number = request.values.get('To', '').strip()
    logging.debug(f"Received webhook: from={from_number}, to={to_number}, body={incoming_msg}")

    phone_number_pattern = re.compile(r'^\+\d{10,15}$')
    if not phone_number_pattern.match(from_number) or not phone_number_pattern.match(to_number):
        logging.error(f"Invalid phone numbers: from={from_number}, to={to_number}")
        return str(MessagingResponse())

    phone_doc = store.db.phone_numbers.find_one({'number': to_number, 'active': True})
    if not phone_doc:
        logging.error(f"No active phone number found for {to_number}")
        return str(MessagingResponse())

    user = store.db.users.find_one({'username': phone_doc['user_username']})
    if not user:
        logging.error(f"No user found for phone {to_number}")
        return str(MessagingResponse())

    msg_data = {
        'phone_id': phone_doc['_id'],
        'direction': 'received',
        'from_number': from_number,
        'to_number': to_number,
        'body': incoming_msg,
        'timestamp': datetime.now(pytz.UTC),
        'priority': 'medium',
        'read': False
    }
    try:
        msg_result = store.db.message_log.insert_one(msg_data)
        logging.debug(f"Logged incoming message: {msg_result.inserted_id}")
    except Exception as e:
        logging.error(f"Failed to log incoming message: {str(e)}")
        return str(MessagingResponse())

    incoming_lang = detect_language(incoming_msg)
    receive_language = user.get('receive_language', 'English')
    translated_msg = translate_message(incoming_msg, incoming_lang, receive_language)

    store.db.message_log.update_one(
        {'_id': msg_result.inserted_id},
        {'$set': {'body_translated': translated_msg}}
    )

    try:
        priority = classify_priority(incoming_msg, from_number, user)
        store.db.message_log.update_one(
            {'_id': msg_result.inserted_id},
            {'$set': {'priority': priority}}
        )
        logging.debug(f"Classified message {msg_result.inserted_id} as {priority}")
    except Exception as e:
        logging.error(f"Failed to classify message {msg_result.inserted_id}: {str(e)}")
        priority = 'medium'

    high_type = None
    if priority == 'high':
        try:
            high_type = classify_high_type(incoming_msg)
            store.db.message_log.update_one(
                {'_id': msg_result.inserted_id},
                {'$set': {'high_type': high_type}}
            )
            logging.debug(f"Classified high_type for message {msg_result.inserted_id} as {high_type}")
        except Exception as e:
            logging.error(f"Failed to classify high_type for message {msg_result.inserted_id}: {str(e)}")
            high_type = 'other'

    socketio.emit('new_message', {
        'direction': 'received',
        'from_number': from_number,
        'to_number': to_number,
        'body': translated_msg,
        'original_body': incoming_msg,
        'timestamp': msg_data['timestamp'].isoformat(),
        'id': str(msg_result.inserted_id),
        'priority': priority,
        'high_type': high_type
    }, room=f"user_{str(phone_doc['_id'])}")

    # Get user effective mode (applies globally, but DND selective)
    effective_mode = get_effective_sending_mode(user)
    mode_value = effective_mode.get('mode', 'high_medium')

    # Base should_forward on mode and priority (global filter for non-DND modes)
    should_forward = False
    if mode_value == 'all':
        should_forward = True
    elif mode_value == 'high_medium' and priority in ['high', 'medium']:
        should_forward = True
    elif mode_value == 'high' and priority == 'high':
        should_forward = True
    elif mode_value == 'dnd':
        should_forward = True  # Allow check, but skip personal inside

    if should_forward:
        try:
            query = {
                'phone_id': phone_doc['_id'],
                'status': 'active',
                '$or': [
                    {
                        'from_number': from_number,
                        '$or': [
                            {'start_time': {'$lte': datetime.now(pytz.UTC)}, 'end_time': {'$gte': datetime.now(pytz.UTC)}},
                            {'start_time': {'$lte': datetime.now(pytz.UTC)}, 'end_time': None},
                            {'start_time': None, 'end_time': {'$gte': datetime.now(pytz.UTC)}},
                            {'start_time': None, 'end_time': None}
                        ]
                    },
                    {
                        'from_number': None,
                        '$or': [
                            {'start_time': {'$lte': datetime.now(pytz.UTC)}, 'end_time': {'$gte': datetime.now(pytz.UTC)}},
                            {'start_time': {'$lte': datetime.now(pytz.UTC)}, 'end_time': None},
                            {'start_time': None, 'end_time': {'$gte': datetime.now(pytz.UTC)}},
                            {'start_time': None, 'end_time': None}
                        ]
                    }
                ]
            }

            forwarding_rules = list(store.db.forwarding_rules.find(query).sort([('from_number', -1), ('created_at', -1)]))
            logging.debug(f"Found {len(forwarding_rules)} forwarding rule(s) for {to_number}")

            if forwarding_rules:
                for forwarding_rule in forwarding_rules:
                    if forwarding_rule['forward_to'] != from_number:
                        if mode_value == 'dnd' and forwarding_rule['forward_to'] == user.get('personal_phone'):
                            logging.info(f"Skipping forward to personal {forwarding_rule['forward_to']} due to DND")
                            continue
                        rule_priority_mode = forwarding_rule.get('priority_mode', 'all')
                        rule_should_forward = False
                        if rule_priority_mode == 'all':
                            rule_should_forward = True
                        elif rule_priority_mode == 'high_medium' and priority in ['high', 'medium']:
                            rule_should_forward = True
                        elif rule_priority_mode == 'high' and priority == 'high' and (
                                not forwarding_rule.get('high_filters') or high_type in forwarding_rule[
                            'high_filters']):
                            rule_should_forward = True

                        if rule_should_forward:
                            message_to_forward = translated_msg if forwarding_rule['forward_to'] == user.get(
                                'personal_phone') else incoming_msg
                            client = Client(phone_doc['account_sid'], phone_doc['auth_token'])
                            forwarded_msg = client.messages.create(
                                body=message_to_forward,
                                from_=to_number,
                                to=forwarding_rule['forward_to']
                            )
                            store.db.message_log.insert_one({
                                'phone_id': phone_doc['_id'],
                                'direction': 'forwarded',
                                'from_number': to_number,
                                'to_number': forwarding_rule['forward_to'],
                                'body': message_to_forward,
                                'original_body': incoming_msg if message_to_forward != incoming_msg else None,
                                'timestamp': datetime.now(pytz.UTC),
                                'message_id': forwarded_msg.sid
                            })
                            logging.info(
                                f"Forwarded message to {forwarding_rule['forward_to']} (Message ID: {forwarded_msg.sid})")
        except Exception as e:
            logging.error(f"Error forwarding message: {str(e)}")

    upcoming.analyze_for_event(str(phone_doc['_id']), str(msg_result.inserted_id), 'received')
    return str(MessagingResponse())


@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    to_number = request.form.get('to_number')
    body = request.form.get('body')
    if not to_number or not body:
        return jsonify({'error': 'To number and body are required'}), 400

    phone_number_pattern = re.compile(r'^\+\d{10,15}$')
    if not phone_number_pattern.match(to_number):
        return jsonify({'error': 'Invalid phone number format'}), 400

    phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
    if not phone_doc:
        return jsonify({'error': 'No active phone number found'}), 400

    user = store.db.users.find_one({'_id': ObjectId(session.get('user_id'))})
    if not user:
        return jsonify({'error': 'User not found'}), 400

    send_language = user.get('send_language', 'English')
    message_lang = detect_language(body)
    translated_body = translate_message(body, message_lang, send_language)

    try:
        client = Client(phone_doc['account_sid'], phone_doc['auth_token'])
        message = client.messages.create(
            body=translated_body,
            from_=phone_doc['number'],
            to=to_number
        )
        current_time = datetime.now(pytz.UTC)
        msg_result = store.db.message_log.insert_one({
            'phone_id': ObjectId(selected_phone_id),
            'direction': 'sent',
            'from_number': phone_doc['number'],
            'to_number': to_number,
            'body': translated_body,
            'original_body': body if translated_body != body else None,
            'timestamp': current_time,
            'message_id': message.sid,
            'priority': 'medium'
        })
        socketio.emit('new_message', {
            'direction': 'sent',
            'from_number': phone_doc['number'],
            'to_number': to_number,
            'body': translated_body,
            'original_body': body if translated_body != body else None,
            'timestamp': current_time.isoformat(),
            'id': str(msg_result.inserted_id)
        }, room=f"user_{selected_phone_id}")

        upcoming.analyze_for_event(selected_phone_id, str(msg_result.inserted_id), 'sent')
        return jsonify({'success': True, 'message_id': str(msg_result.inserted_id)}), 200
    except Exception as e:
        logging.error(f"Failed to send message to {to_number}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/delete_group/<group_id>', methods=['POST'])
@login_required
def delete_group(group_id):
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    try:
        result = store.db.upcoming_events.delete_many({
            'group_id': ObjectId(group_id),
            'phone_id': ObjectId(selected_phone_id)
        })
        if result.deleted_count > 0:
            socketio.emit('group_deleted', {'group_id': group_id}, room=f"user_{selected_phone_id}")
            return jsonify({'success': True, 'deleted_count': result.deleted_count}), 200
        else:
            return jsonify({'error': 'Event group not found'}), 404
    except Exception as e:
        logger.error(f"Error deleting event group {group_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/delete_all_events', methods=['POST'])
@login_required
def delete_all_events():
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400
    try:
        result = store.db.upcoming_events.delete_many({
            'phone_id': ObjectId(selected_phone_id)
        })
        socketio.emit('all_events_deleted', {'count': result.deleted_count}, room=f"user_{selected_phone_id}")
        return jsonify({'success': True, 'deleted_count': result.deleted_count}), 200
    except Exception as e:
        logger.error(f"Error deleting all events: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/delete_event/<event_id>', methods=['POST'])
@login_required
def delete_event(event_id):
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400
    try:
        result = store.db.upcoming_events.delete_one({
            '_id': ObjectId(event_id),
            'phone_id': ObjectId(selected_phone_id)
        })
        if result.deleted_count > 0:
            socketio.emit('event_deleted', {'event_id': event_id}, room=f"user_{selected_phone_id}")
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': 'Event not found'}), 404
    except Exception as e:
        logger.error(f"Error deleting event {event_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


# if __name__ == '__main__':
#     if not users_collection.find_one({'username': 'admin'}):
#         users_collection.insert_one({
#             'username': 'admin',
#             'name': 'Administrator',
#             'password': bcrypt.generate_password_hash('admin123').decode('utf-8'),
#             'is_admin': True,
#             'selected_phone_id': None,
#             'receive_language': 'English',
#             'send_language': 'English'
#         })
#     try:
#         socketio.run(app, debug=True, port=5000)  # Use socketio.run instead of app.run
#     finally:
#         scheduler.shutdown()

@socketio.on('heartbeat')
def handle_heartbeat(data):
    logger.debug("Received heartbeat")

if __name__ == '__main__':
    if not users_collection.find_one({'username': 'admin'}):
        users_collection.insert_one({
            'username': 'admin',
            'name': 'Administrator',
            'password': bcrypt.generate_password_hash('admin123').decode('utf-8'),
            'is_admin': True,
            'selected_phone_id': None,
            'receive_language': 'English',
            'send_language': 'English'
        })
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=True)
    finally:
        scheduler.shutdown()