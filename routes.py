from flask import request, flash, redirect, url_for, session, render_template
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import pytz
from db import store
from twilio.rest import Client
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
import logging
import re
from extensions import socketio
import json
from extensions import socketio  # Add this import
from ai import HIGH_TYPES
# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
logging.getLogger('pymongo').setLevel(logging.INFO)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

# Initialize APScheduler with MongoDBJobStore
scheduler = BackgroundScheduler(jobstores={
    'mongo': MongoDBJobStore(database='Floxy', collection='apscheduler_jobs', client=store.db.client)
}, timezone=pytz.UTC)

# Run cleanup on startup
def cleanup_overdue_messages():
    current_time = datetime.now(pytz.UTC)
    grace_period = current_time - timedelta(minutes=5)
    overdue_messages = store.db.scheduled_messages.find({
        'status': 'pending',
        'schedule_time': {'$lte': grace_period}
    })
    count = 0
    for msg in overdue_messages:
        store.db.scheduled_messages.update_one(
            {'_id': msg['_id']},
            {'$set': {'status': 'failed', 'error': 'Scheduled time passed without execution', 'updated_at': current_time}}
        )
        try:
            scheduler.remove_job(f"send_{str(msg['_id'])}")
            logger.info(f"Removed job send_{str(msg['_id'])}")
        except:
            logger.debug(f"Job send_{str(msg['_id'])} already removed")
        logger.info(f"Marked message {msg['_id']} as failed due to overdue schedule time")
        count += 1
    logger.debug(f"Startup cleanup processed {count} overdue messages")

# New: Cleanup expired forwarding rules
def cleanup_expired_rules():
    current_time = datetime.now(pytz.UTC)
    expired_rules = store.db.forwarding_rules.find({
        'status': 'active',
        'end_time': {'$exists': True, '$lte': current_time}
    })
    count = 0
    for rule in expired_rules:
        store.db.forwarding_rules.update_one(
            {'_id': rule['_id']},
            {'$set': {'status': 'expired', 'expired_at': current_time}}
        )
        # Emit SocketIO event
        socketio.emit('rule_status_update', {
            'rule_id': str(rule['_id']),
            'status': 'expired',
            'expired_at': current_time.isoformat()
        }, room=f"user_{rule['phone_id']}")
        logger.info(f"Marked rule {rule['_id']} as expired")
        count += 1
    logger.debug(f"Cleanup processed {count} expired rules")

cleanup_overdue_messages()
cleanup_expired_rules()
scheduler.add_job(
    cleanup_overdue_messages,
    'interval',
    minutes=1,
    id='cleanup_overdue_messages',
    replace_existing=True
)
scheduler.add_job(
    cleanup_expired_rules,
    'interval',
    minutes=1,
    id='cleanup_expired_rules',
    replace_existing=True
)
scheduler.start()

def send_message_job(message_id):
    try:
        msg = store.db.scheduled_messages.find_one({'_id': ObjectId(message_id)})
        if not msg:
            logger.error(f"No message with ID {message_id}")
            try:
                scheduler.remove_job(f"send_{message_id}")
                logger.info(f"Removed job send_{message_id} due to missing message")
            except:
                pass
            return

        phone_doc = store.db.phone_numbers.find_one({'_id': msg['phone_id'], 'active': True})
        if not phone_doc:
            logger.error(f"No active phone number for ID {msg['phone_id']}")
            store.db.scheduled_messages.update_one(
                {'_id': ObjectId(message_id)},
                {'$set': {'status': 'failed', 'error': 'No active phone number', 'updated_at': datetime.now(pytz.UTC)}}
            )
            socketio.emit('message_status_update', {
                'message_id': str(msg['_id']),
                'status': 'failed',
                'error': 'No active phone number',
                'updated_at': datetime.now(pytz.UTC).isoformat()
            }, room=f"user_{msg['phone_id']}")
            try:
                scheduler.remove_job(f"send_{message_id}")
                logger.info(f"Removed job send_{message_id} due to no active phone")
            except:
                pass
            return

        client = Client(phone_doc['account_sid'], phone_doc['auth_token'])
        message = client.messages.create(
            body=msg['content'],
            from_=phone_doc['number'],
            to=msg['to_number']
        )
        logger.debug(f"Message sent with ID: {message.sid}")

        current_time = datetime.now(pytz.UTC)
        store.db.scheduled_messages.update_one(
            {'_id': ObjectId(message_id)},
            {'$set': {'status': 'sent', 'sent_at': current_time, 'updated_at': current_time, 'message_id': message.sid}}
        )
        logger.info(f"Sent SMS to {msg['to_number']}: {msg['content']} (Message ID: {message.sid})")

        # Log to message_log (scoped to phone_id)
        store.db.message_log.insert_one({
            'phone_id': msg['phone_id'],
            'direction': 'sent',
            'from_number': phone_doc['number'],
            'to_number': msg['to_number'],
            'body': msg['content'],
            'timestamp': current_time,
            'message_id': message.sid,
            'priority': 'medium'
        })

        # Emit real-time updates
        socketio.emit('message_status_update', {
            'message_id': str(msg['_id']),
            'status': 'sent',
            'sent_at': current_time.isoformat()
        }, room=f"user_{msg['phone_id']}")

        socketio.emit('new_message', {
            'direction': 'sent',
            'from_number': phone_doc['number'],
            'to_number': msg['to_number'],
            'body': msg['content'],
            'timestamp': current_time.isoformat()
        }, room=f"user_{msg['phone_id']}")

        try:
            scheduler.remove_job(f"send_{message_id}")
            logger.info(f"Removed job send_{message_id} after successful send")
        except:
            logger.debug(f"Job send_{message_id} already removed")
    except Exception as e:
        logger.error(f"Failed to send SMS {message_id}: {str(e)}", exc_info=True)
        current_time = datetime.now(pytz.UTC)
        store.db.scheduled_messages.update_one(
            {'_id': ObjectId(message_id)},
            {'$set': {'status': 'failed', 'error': str(e), 'updated_at': current_time}}
        )
        # Emit update for failure
        socketio.emit('message_status_update', {
            'message_id': str(msg['_id']),
            'status': 'failed',
            'error': str(e),
            'updated_at': current_time.isoformat()
        }, room=f"user_{msg['phone_id']}")
        try:
            scheduler.remove_job(f"send_{message_id}")
            logger.info(f"Removed job send_{message_id} due to send failure")
        except:
            logger.debug(f"Job send_{message_id} already removed")


def sms_scheduling(to_number=''):
    if 'user_id' not in session:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return redirect(url_for('select_phone'))

    phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
    if not phone_doc:
        flash('No active phone number found.', 'error')
        return redirect(url_for('select_phone'))

    # Queries scoped to phone_id for user isolation
    pending_messages = list(store.db.scheduled_messages.find({
        'phone_id': ObjectId(selected_phone_id),
        'status': 'pending'
    }).sort('schedule_time', 1))

    past_messages = list(store.db.scheduled_messages.find({
        'phone_id': ObjectId(selected_phone_id),
        'status': {'$in': ['sent', 'failed', 'cancelled']},
        'updated_at': {'$gte': datetime.now(pytz.UTC).replace(hour=0, minute=0, second=0, microsecond=0)}
    }).sort('updated_at', -1))

    contacts = list(store.db.contacts.find({'phone_id': ObjectId(selected_phone_id)}).sort('alias', 1))

    logger.debug(f"Pending messages: {len(pending_messages)}, Past messages: {len(past_messages)}, Contacts: {len(contacts)} for phone_id: {selected_phone_id}")
    return render_template(
        'sms_scheduling.html',
        pending_messages=pending_messages,
        past_messages=past_messages,
        contacts=contacts,
        phone_number=phone_doc['number'],
        active_section='sms_scheduling',
        to_number=to_number
    )

def schedule_message():
    if 'user_id' not in session:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return redirect(url_for('select_phone'))

    to_number = request.form.get('to_number')
    content = request.form.get('message_content')
    send_type = request.form.get('send_type')
    schedule_date = request.form.get('schedule_date')
    schedule_time = request.form.get('schedule_time')

    if not to_number or not content:
        flash('Phone number and message content are required.', 'error')
        return redirect(url_for('sms_scheduling_route'))

    if len(content) > 160:
        flash('Message content must not exceed 160 characters.', 'error')
        return redirect(url_for('sms_scheduling_route'))

    phone_number_pattern = re.compile(r'^\+\d{10,15}$')
    if not phone_number_pattern.match(to_number):
        flash('Invalid phone number format. Use + followed by 10-15 digits.', 'error')
        return redirect(url_for('sms_scheduling_route'))

    phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
    if not phone_doc:
        flash('No active phone number found.', 'error')
        return redirect(url_for('sms_scheduling_route'))

    if to_number == phone_doc['number']:
        flash('Cannot schedule a message to the same number as the selected phone.', 'error')
        return redirect(url_for('sms_scheduling_route'))

    msg_data = {
        'phone_id': ObjectId(selected_phone_id),
        'to_number': to_number,
        'content': content,
        'status': 'pending',
        'created_at': datetime.now(pytz.UTC)
    }

    try:
        if send_type == 'instant':
            client = Client(phone_doc['account_sid'], phone_doc['auth_token'])
            message = client.messages.create(
                body=content,
                from_=phone_doc['number'],
                to=to_number
            )
            current_time = datetime.now(pytz.UTC)
            msg_data['status'] = 'sent'
            msg_data['sent_at'] = current_time
            msg_data['updated_at'] = current_time
            msg_data['message_id'] = message.sid
            result = store.db.scheduled_messages.insert_one(msg_data)

            # Log to message_log (scoped to phone_id)
            store.db.message_log.insert_one({
                'phone_id': ObjectId(selected_phone_id),
                'direction': 'sent',
                'from_number': phone_doc['number'],
                'to_number': to_number,
                'body': content,
                'timestamp': current_time,
                'message_id': message.sid,
                'priority': 'medium'
            })

            flash('Message sent successfully.', 'success')
            logger.info(f"Sent instant SMS to {to_number}: {content} (Message ID: {message.sid})")

            # Emit for instant send
            socketio.emit('message_status_update', {
                'message_id': str(result.inserted_id),
                'status': 'sent',
                'sent_at': current_time.isoformat()
            }, room=f"user_{selected_phone_id}")

            socketio.emit('new_message', {
                'direction': 'sent',
                'from_number': phone_doc['number'],
                'to_number': to_number,
                'body': content,
                'timestamp': current_time.isoformat()
            }, room=f"user_{selected_phone_id}")
        else:
            if not schedule_date or not schedule_time:
                flash('Schedule date and time are required for scheduled messages.', 'error')
                return redirect(url_for('sms_scheduling_route'))

            schedule_datetime = datetime.strptime(f"{schedule_date} {schedule_time}", '%Y-%m-%d %H:%M')
            schedule_datetime = pytz.UTC.localize(schedule_datetime)
            current_time = datetime.now(pytz.UTC)
            min_schedule_time = current_time + timedelta(minutes=2)  # 2-minute buffer
            logger.debug(f"schedule_datetime: {schedule_datetime}, current_time: {current_time}, min_schedule_time: {min_schedule_time}")

            if schedule_datetime <= min_schedule_time:
                flash('Schedule time must be at least 2 minutes in the future.', 'error')
                return redirect(url_for('sms_scheduling_route'))

            msg_data['schedule_time'] = schedule_datetime
            result = store.db.scheduled_messages.insert_one(msg_data)
            scheduler.add_job(
                send_message_job,
                'date',
                run_date=schedule_datetime,
                args=[str(result.inserted_id)],
                id=f"send_{str(result.inserted_id)}",
                replace_existing=True
            )
            logger.info(f"Scheduled job send_{str(result.inserted_id)} for {schedule_datetime}")
            flash('Message scheduled successfully.', 'success')
    except ValueError as e:
        logger.error(f"Invalid schedule date or time: {str(e)}")
        flash('Invalid schedule date or time.', 'error')
        return redirect(url_for('sms_scheduling_route'))
    except Exception as e:
        logger.error(f"Error scheduling message: {str(e)}", exc_info=True)
        flash(f'Error scheduling message: {str(e)}', 'error')
        return redirect(url_for('sms_scheduling_route'))

    return redirect(url_for('sms_scheduling_route'))

def cancel_message(msg_id):
    if 'user_id' not in session:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return redirect(url_for('select_phone'))

    current_time = datetime.now(pytz.UTC)
    result = store.db.scheduled_messages.update_one(
        {'_id': ObjectId(msg_id), 'phone_id': ObjectId(selected_phone_id), 'status': 'pending'},
        {'$set': {'status': 'cancelled', 'cancelled_at': current_time, 'updated_at': current_time}}
    )
    if result.modified_count == 0:
        flash('Message not found or already processed.', 'error')
        logger.debug(f"Failed to cancel message {msg_id}: not found or already processed")
    else:
        try:
            scheduler.remove_job(f"send_{msg_id}")
            logger.info(f"Removed job send_{msg_id}")
        except:
            logger.debug(f"Job send_{msg_id} already removed")
        flash('Message cancelled successfully.', 'success')
        # Emit update for cancel
        socketio.emit('message_status_update', {
            'message_id': msg_id,
            'status': 'cancelled',
            'updated_at': current_time.isoformat()
        }, room=f"user_{selected_phone_id}")
    return redirect(url_for('sms_scheduling_route'))

def sms_forwarding(from_number=''):
    if 'user_id' not in session:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return redirect(url_for('select_phone'))

    phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
    if not phone_doc:
        flash('No active phone number found.', 'error')
        return redirect(url_for('select_phone'))

    user = store.db.users.find_one({'_id': ObjectId(session['user_id'])})
    personal_phone = user.get('personal_phone', '')

    current_time = datetime.now(pytz.UTC)
    upcoming_rules = list(store.db.forwarding_rules.find({
        'phone_id': ObjectId(selected_phone_id),
        'status': 'active',
        'start_time': {'$gt': current_time}
    }).sort('start_time', 1))

    active_rules = list(store.db.forwarding_rules.find({
        'phone_id': ObjectId(selected_phone_id),
        'status': 'active',
        '$or': [
            {'start_time': {'$lte': current_time}, 'end_time': {'$gt': current_time}},
            {'start_time': {'$lte': current_time}, 'end_time': None},
            {'start_time': None, 'end_time': {'$gt': current_time}},
            {'start_time': None, 'end_time': None}
        ]
    }).sort('created_at', -1))

    past_rules = list(store.db.forwarding_rules.find({
        'phone_id': ObjectId(selected_phone_id),
        'status': {'$in': ['stopped', 'expired']},
        'stopped_at': {'$gte': current_time.replace(hour=0, minute=0,second=0,microsecond=0)},
        'expired_at': {'$gte': current_time.replace(hour=0, minute=0,second=0,microsecond=0)}
    }).sort('stopped_at', -1))

    contacts = list(store.db.contacts.find({'phone_id': ObjectId(selected_phone_id)}).sort('alias', 1))
    logger.debug(f"Fetched {len(upcoming_rules)} upcoming rules, {len(active_rules)} active, {len(past_rules)} past, {len(contacts)} contacts for phone_id: {selected_phone_id}")
    return render_template(
        'sms_forwarding.html',
        upcoming_rules=upcoming_rules,
        active_rules=active_rules,
        past_rules=past_rules,
        contacts=contacts,
        phone_number=phone_doc['number'],
        active_section='sms_forwarding',
        from_number=from_number,
        personal_phone=personal_phone,
        high_types=HIGH_TYPES  # New: Pass high_types for the checkboxes
    )

def add_forwarding_rule():
    if 'user_id' not in session:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return redirect(url_for('sms_forwarding_route'))

    from_number = request.form.get('from_number').strip() if request.form.get('from_number') else None
    forward_to = request.form.get('forward_to').strip() if request.form.get('forward_to') else None
    start_date = request.form.get('start_date')
    start_time = request.form.get('start_time')
    end_date = request.form.get('end_date')
    end_time = request.form.get('end_time')
    forward_type = request.form.get('forward_type')
    priority_mode = request.form.get('priority_mode', 'all')  # New: Get priority_mode, default 'all'
    high_types = request.form.getlist('high_types[]') if priority_mode == 'high' else []  # New: Get high filters if 'high'

    if not forward_to:
        flash('Forward-to number is required.', 'error')
        return redirect(url_for('sms_forwarding_route'))

    phone_number_pattern = re.compile(r'^\+\d{10,15}$')
    if from_number and not phone_number_pattern.match(from_number):
        flash('Invalid from number format. Use + followed by 10-15 digits.', 'error')
        return redirect(url_for('sms_forwarding_route'))

    if not phone_number_pattern.match(forward_to):
        flash('Invalid forward-to number format. Use + followed by 10-15 digits.', 'error')
        return redirect(url_for('sms_forwarding_route'))

    try:
        phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
        if not phone_doc:
            logger.error(f"No active phone found for selected_phone_id: {selected_phone_id}")
            flash('No active phone number found.', 'error')
            return redirect(url_for('sms_forwarding_route'))

        if forward_to == phone_doc['number']:
            logger.warning(f"Attempted to forward to same number as selected phone: {forward_to}")
            flash('Cannot forward to the same number as the selected phone.', 'error')
            return redirect(url_for('sms_forwarding_route'))

        if forward_type != 'all' and from_number and forward_to == from_number:
            logger.warning(f"Attempted to forward from {from_number} to itself")
            flash('Cannot forward from a number to itself.', 'error')
            return redirect(url_for('sms_forwarding_route'))
    except Exception as e:
        logger.error(f"Error checking phone number: {str(e)}", exc_info=True)
        flash('Error validating phone number.', 'error')
        return redirect(url_for('sms_forwarding_route'))

    rule_data = {
        'phone_id': ObjectId(selected_phone_id),
        'from_number': from_number if forward_type != 'all' else None,
        'forward_to': forward_to,
        'status': 'active',
        'created_at': datetime.now(pytz.UTC),
        'priority_mode': priority_mode  # New: Add priority_mode
    }
    if high_types:
        rule_data['high_filters'] = high_types  # New: Add high_filters if provided

    try:
        current_time = datetime.now(pytz.UTC)
        if start_date and start_time:
            start_datetime = datetime.strptime(f"{start_date} {start_time}", '%Y-%m-%d %H:%M')
            start_datetime = pytz.UTC.localize(start_datetime)
            if start_datetime <= current_time:
                flash('Start time must be in the future.', 'error')
                return redirect(url_for('sms_forwarding_route'))
            rule_data['start_time'] = start_datetime
        elif forward_type == 'start_now':
            rule_data['start_time'] = current_time
        else:
            rule_data['start_time'] = None

        if end_date and end_time:
            end_datetime = datetime.strptime(f"{end_date} {end_time}", '%Y-%m-%d %H:%M')
            end_datetime = pytz.UTC.localize(end_datetime)
            if end_datetime <= current_time:
                flash('End time must be in the future.', 'error')
                return redirect(url_for('sms_forwarding_route'))
            if 'start_time' in rule_data and rule_data['start_time'] and end_datetime <= rule_data['start_time']:
                flash('End time must be after start time.', 'error')
                return redirect(url_for('sms_forwarding_route'))
            rule_data['end_time'] = end_datetime
        elif forward_type in ['forever', 'all']:
            rule_data['end_time'] = None
        else:
            rule_data['end_time'] = None

        if forward_type == 'all':
            rule_data['from_number'] = None
            rule_data['start_time'] = None
            rule_data['end_time'] = None

        # Check for duplicate or overlapping rules (scoped to phone_id)
        query = {
            'phone_id': ObjectId(selected_phone_id),
            'forward_to': forward_to,
            'from_number': rule_data['from_number'],
            'status': 'active'
        }

        time_conditions = []
        if 'start_time' in rule_data and rule_data['start_time'] and 'end_time' in rule_data and rule_data['end_time']:
            time_conditions.append({
                'start_time': {'$lte': rule_data['end_time']},
                'end_time': {'$gte': rule_data['start_time']}
            })
            time_conditions.append({
                'start_time': {'$lte': rule_data['end_time']},
                'end_time': None
            })
            time_conditions.append({
                'start_time': None,
                'end_time': {'$gte': rule_data['start_time']}
            })
        elif 'start_time' in rule_data and rule_data['start_time']:
            time_conditions.append({'end_time': {'$gte': rule_data['start_time']}})
            time_conditions.append({'end_time': None})
        elif 'end_time' in rule_data and rule_data['end_time']:
            time_conditions.append({'start_time': {'$lte': rule_data['end_time']}})
            time_conditions.append({'start_time': None})

        if time_conditions:
            query['$or'] = time_conditions
        else:
            query['start_time'] = None
            query['end_time'] = None

        existing_rule = store.db.forwarding_rules.find_one(query)
        if existing_rule:
            logger.warning(f"Duplicate rule detected: {query}")
            flash('A forwarding rule with the same numbers and overlapping time frame already exists.', 'error')
            return redirect(url_for('sms_forwarding_route'))

        # Insert new rule
        result = store.db.forwarding_rules.insert_one(rule_data)
        flash('Forwarding rule added successfully.', 'success')
        logger.info(
            f"Added forwarding rule: from={rule_data['from_number'] or 'All'}, to={rule_data['forward_to']}, priority_mode={rule_data['priority_mode']}, start={rule_data.get('start_time')}, end={rule_data.get('end_time')}")

        # Prepare JSON-serializable rule data for SocketIO
        socket_rule_data = {
            '_id': str(result.inserted_id),
            'phone_id': str(rule_data['phone_id']),
            'from_number': rule_data['from_number'] or 'All',
            'forward_to': rule_data['forward_to'],
            'priority_mode': rule_data['priority_mode'],
            'high_filters': rule_data.get('high_filters', []),  # New: Add high_filters
            'status': rule_data['status'],
            'created_at': rule_data['created_at'].isoformat(),
            'start_time': rule_data['start_time'].isoformat() if rule_data.get('start_time') else None,
            'end_time': rule_data['end_time'].isoformat() if rule_data.get('end_time') else None
        }
        socketio.emit('rule_status_update', {
            'rule_id': str(result.inserted_id),
            'status': 'active',
            'rule_data': socket_rule_data
        }, room=f"user_{selected_phone_id}")

    except ValueError as e:
        logger.error(f"Invalid date or time format: {str(e)}")
        flash('Invalid date or time format.', 'error')
        return redirect(url_for('sms_forwarding_route'))
    except Exception as e:
        logger.error(f"Error adding forwarding rule: {str(e)}", exc_info=True)
        flash(f'Error adding forwarding rule: {str(e)}', 'error')
        return redirect(url_for('sms_forwarding_route'))

    return redirect(url_for('sms_forwarding_route'))

def stop_forwarding_rule(rule_id):
    if 'user_id' not in session:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return redirect(url_for('sms_forwarding_route'))

    current_time = datetime.now(pytz.UTC)
    result = store.db.forwarding_rules.update_one(
        {'_id': ObjectId(rule_id), 'phone_id': ObjectId(selected_phone_id), 'status': 'active'},
        {'$set': {'status': 'stopped', 'stopped_at': current_time}}
    )
    if result.modified_count == 0:
        flash('Rule not found or already stopped.', 'error')
        logger.debug(f"Failed to stop rule {rule_id}: not found or already stopped")
    else:
        flash('Forwarding rule stopped successfully.', 'success')
        logger.info(f"Stopped forwarding rule {rule_id}")
        # Emit update for stop
        socketio.emit('rule_status_update', {
            'rule_id': rule_id,
            'status': 'stopped',
            'stopped_at': current_time.isoformat()
        }, room=f"user_{selected_phone_id}")
    return redirect(url_for('sms_forwarding_route'))


def delete_chat(phone_number):
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return jsonify({'error': 'No phone number selected'}), 400

    phone_number_pattern = re.compile(r'^\+\d{10,15}$')
    if not phone_number_pattern.match(phone_number):
        return jsonify({'error': 'Invalid phone number format'}), 400

    # Delete all messages in the conversation (scoped to phone_id)
    delete_result = store.db.message_log.delete_many({
        'phone_id': ObjectId(selected_phone_id),
        '$or': [
            {'from_number': phone_number, 'direction': 'received'},
            {'to_number': phone_number, 'direction': 'sent'}
        ]
    })

    # Also delete the contact if it exists to prevent reappearance (scoped to phone_id)
    contact_delete_result = store.db.contacts.delete_one({
        'phone_id': ObjectId(selected_phone_id),
        'phone_number': phone_number
    })

    if delete_result.deleted_count > 0 or contact_delete_result.deleted_count > 0:
        # Emit socket events for deletion
        socketio.emit('chat_deleted', {'phone_number': phone_number}, room=f"user_{selected_phone_id}")
        # Also emit contact_deleted if contact was deleted
        if contact_delete_result.deleted_count > 0:
            socketio.emit('contact_deleted', {'phone_number': phone_number}, room=f"user_{selected_phone_id}")
        return jsonify({'success': True}), 200
    else:
        return jsonify({'error': 'No chat found to delete'}), 404

def add_contact():
    if 'user_id' not in session:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return redirect(url_for('select_phone'))

    phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(selected_phone_id), 'active': True})
    if not phone_doc:
        flash('No active phone number found.', 'error')
        return redirect(url_for('select_phone'))

    if request.method == 'POST':
        alias = request.form.get('alias').strip()
        phone_number = request.form.get('phone_number').strip()
        label = request.form.get('label') or 'N/A'

        if not alias or not phone_number:
            flash('Alias and phone number are required.', 'error')
            return redirect(url_for('contacts'))

        if alias == phone_number:
            flash('Alias and phone number must be different.', 'error')
            return redirect(url_for('contacts'))

        phone_number_pattern = re.compile(r'^\+\d{10,15}$')
        if not phone_number_pattern.match(phone_number):
            flash('Invalid phone number format. Use + followed by 10-15 digits.', 'error')
            return redirect(url_for('contacts'))

        if phone_number == phone_doc['number']:
            flash('Cannot add a contact with the same number as the selected phone.', 'error')
            return redirect(url_for('contacts'))

        # Check for existing contact (scoped to phone_id)
        existing_contact = store.db.contacts.find_one({
            'phone_id': ObjectId(selected_phone_id),
            'phone_number': phone_number
        })

        try:
            current_time = datetime.now(pytz.UTC)
            if existing_contact:
                # Update existing contact
                store.db.contacts.update_one(
                    {'_id': existing_contact['_id']},
                    {'$set': {'alias': alias, 'label': label, 'updated_at': current_time}}
                )
                logger.info(f"Updated contact: {alias} ({phone_number}) with label: {label}")
            else:
                # Check for duplicate alias (case-insensitive, scoped to phone_id)
                if store.db.contacts.find_one({
                    'phone_id': ObjectId(selected_phone_id),
                    'alias': {'$regex': f'^{re.escape(alias)}$', '$options': 'i'}
                }):
                    flash('Contact with this alias already exists.', 'error')
                    return redirect(url_for('contacts'))

                # Insert new contact
                contact_data = {
                    'phone_id': ObjectId(selected_phone_id),
                    'alias': alias,
                    'phone_number': phone_number,
                    'label': label,
                    'created_at': current_time
                }
                store.db.contacts.insert_one(contact_data)
                logger.info(f"Added contact: {alias} ({phone_number})")

            # Emit real-time update for contact add/update
            socketio.emit('update_contact', {
                'phone_number': phone_number,
                'alias': alias,
                'label': label
            }, room=f"user_{selected_phone_id}")

            flash('Contact added/updated successfully.', 'success')
            return redirect(url_for('inbox'))

        except Exception as e:
            logger.error(f"Error adding/updating contact: {str(e)}", exc_info=True)
            flash(f'Error adding/updating contact: {str(e)}', 'error')
            return redirect(url_for('inbox'))

    # Fetch contacts (scoped to phone_id for user isolation)
    contacts = list(store.db.contacts.find({'phone_id': ObjectId(selected_phone_id)}).sort('alias', 1))
    logger.debug(f"Fetched {len(contacts)} contacts for phone_id: {selected_phone_id}")
    return render_template(
        'contacts.html',
        contacts=contacts,
        phone_number=phone_doc['number'],
        active_section='contacts'
    )

def delete_contact(phone_number):
    if 'user_id' not in session:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return redirect(url_for('select_phone'))

    # Delete contact (scoped to phone_id)
    result = store.db.contacts.delete_one({
        'phone_id': ObjectId(selected_phone_id),
        'phone_number': phone_number
    })
    if result.deleted_count == 0:
        flash('Contact not found.', 'error')
        logger.debug(f"Failed to delete contact {phone_number}: not found")
    else:
        flash('Contact deleted successfully.', 'success')
        logger.info(f"Deleted contact {phone_number}")
        # Emit update for deletion
        socketio.emit('contact_deleted', {
            'phone_number': phone_number
        }, room=f"user_{selected_phone_id}")

    return redirect(url_for('inbox'))

def assign_label():
    if 'user_id' not in session:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))

    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        flash('No phone number selected.', 'error')
        return redirect(url_for('select_phone'))

    if request.method == 'POST':
        phone_number = request.form.get('phone_number')
        label = request.form.get('label')

        if not phone_number or not label:
            return 'Missing data', 400

        contact = store.db.contacts.find_one({'phone_id': ObjectId(selected_phone_id), 'phone_number': phone_number})
        current_time = datetime.now(pytz.UTC)

        if contact:
            store.db.contacts.update_one(
                {'_id': contact['_id']},
                {'$set': {'label': label, 'updated_at': current_time}}
            )
        else:
            store.db.contacts.insert_one({
                'phone_id': ObjectId(selected_phone_id),
                'alias': phone_number,  # Default alias to phone number
                'phone_number': phone_number,
                'label': label,
                'created_at': current_time
            })

        # Emit real-time update for label change
        socketio.emit('update_contact_label', {
            'phone_number': phone_number,
            'label': label
        }, room=f"user_{selected_phone_id}")

        return 'Success', 200

    return 'Invalid method', 405
