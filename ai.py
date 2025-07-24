import os
import json
from openai import AzureOpenAI
from flask import render_template, request, jsonify, session, flash, redirect, url_for
from bson.objectid import ObjectId
from db import store
import logging
from flask_socketio import emit
from extensions import socketio
import re

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv
from openai import AzureOpenAI

# Load environment variables from .env file
load_dotenv()

# Initialize Azure OpenAI client with environment variables
client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION")
)

deployment_name = "gpt-4o"

_priority_prompt = (
    "Classify this SMS as one of: high priority, medium priority, low priority.\n\n"
    "- High priority: Urgent, critical, or time-sensitive messages (e.g., 'ASAP', 'emergency', scheduled events, meetings, or tasks).\n"
    "- Medium priority: Casual or non-urgent messages (e.g., 'hey', 'hi', 'wanna go out tonight').\n"
    "- Low priority: Spam or scam messages, especially those with suspicious intent (e.g., containing words like 'free', 'win', 'prize', or phishing links). Messages like '1', '2', '3', '4' are also considered spam.\n\n"
    "Return JSON: {\"label\": <label>} only."
)

LABELS = ["high priority", "medium priority", "low priority", "spam", "personal", "work", "family", "other"]

_other_prompt = (
    "Classify this SMS into one of the following labels: "
    + ", ".join([label for label in LABELS if label not in ["high priority", "medium priority", "low priority"]])
    + ". Return JSON: {\"label\": <label>} only."
)

def classify_priority(body, from_number, user):
    priority_from = user.get('priority_from', [])
    priority_keywords = [kw.lower() for kw in user.get('priority_keywords', [])]
    spam_ignore_from = user.get('spam_ignore_from', [])
    spam_keywords = [kw.lower() for kw in user.get('spam_keywords', [])]

    if from_number in priority_from:
        return 'high'

    body_lower = body.lower()
    if any(keyword in body_lower for keyword in priority_keywords):
        return 'high'

    if from_number in spam_ignore_from:
        return 'low'

    if any(keyword in body_lower for keyword in spam_keywords):
        return 'low'

    response = client.chat.completions.create(
        model=deployment_name,
        messages=[
            {"role": "system", "content": _priority_prompt},
            {"role": "user", "content": body}
        ],
        response_format={"type": "json_object"}
    )
    result = response.choices[0].message.content
    label = json.loads(result)["label"]
    return label.replace(" priority", "")

def classify_other(body):
    response = client.chat.completions.create(
        model=deployment_name,
        messages=[
            {"role": "system", "content": _other_prompt},
            {"role": "user", "content": body}
        ],
        response_format={"type": "json_object"}
    )
    result = response.choices[0].message.content
    label = json.loads(result)["label"]
    return label

def held_messages():
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
    else:
        flash("No phone number selected.", "error")
        return redirect(url_for('select_phone'))

    spam_messages = list(store.db.message_log.find(
        {'phone_id': ObjectId(selected_phone_id), 'direction': 'received',
         'priority': 'low'} if selected_phone_id else {}
    ).sort('timestamp', -1))
    contacts = list(
        store.db.contacts.find({'phone_id': ObjectId(selected_phone_id)}).sort('alias', 1)) if selected_phone_id else []

    return render_template(
        'held_messages.html',
        user_name=user.get('name'),
        phone_number=phone,
        spam_messages=spam_messages,
        contacts=contacts,
        active_section='held-messages'
    )

def delete_held_messages(message_id=None):
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    try:
        if message_id:
            result = store.db.message_log.delete_one({
                '_id': ObjectId(message_id),
                'phone_id': ObjectId(selected_phone_id),
                'priority': 'low'
            })
            if result.deleted_count > 0:
                socketio.emit('message_deleted', {'message_id': message_id}, room=f"user_{selected_phone_id}",
                              namespace='/')
                return jsonify({'success': True}), 200
            else:
                return jsonify({'error': 'Message not found or not spam'}), 404
        else:
            messages = store.db.message_log.find({
                'phone_id': ObjectId(selected_phone_id),
                'priority': 'low',
                'direction': 'received'
            })
            message_ids = [str(msg['_id']) for msg in messages]
            result = store.db.message_log.delete_many({
                'phone_id': ObjectId(selected_phone_id),
                'priority': 'low',
                'direction': 'received'
            })
            if result.deleted_count > 0:
                for mid in message_ids:
                    socketio.emit('message_deleted', {'message_id': mid}, room=f"user_{selected_phone_id}",
                                  namespace='/')
                socketio.emit('messages_deleted_all', {'count': result.deleted_count}, room=f"user_{selected_phone_id}",
                              namespace='/')
                return jsonify({'success': True, 'deleted_count': result.deleted_count}), 200
            else:
                return jsonify({'error': 'No spam messages found'}), 404
    except Exception as e:
        logger.error(f"Error deleting held messages: {str(e)}")
        return jsonify({'error': str(e)}), 500

def allow_held_message():
    selected_phone_id = session.get('selected_phone_id')
    if not selected_phone_id:
        return jsonify({'error': 'No phone number selected'}), 400

    data = request.get_json()
    message_id = data.get('message_id')
    if not message_id:
        return jsonify({'error': 'Message ID required'}), 400

    try:
        result = store.db.message_log.update_one(
            {'_id': ObjectId(message_id), 'phone_id': ObjectId(selected_phone_id), 'priority': 'low'},
            {'$set': {'priority': 'medium'}}
        )
        if result.modified_count > 0:
            emit('message_allowed', {'message_id': message_id}, room=f"user_{selected_phone_id}")
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': 'Message not found or not spam'}), 404
    except Exception as e:
        logger.error(f"Error allowing message {message_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

HIGH_TYPES = ["emergency", "meeting", "family_meeting", "appointment", "task", "deadline", "bank", "other"]

_high_type_prompt = (
    "Classify this high-priority SMS into one of: "
    + ", ".join(HIGH_TYPES) +
    ". Return JSON: {\"label\": <label>} only."
)

def classify_high_type(body):
    response = client.chat.completions.create(
        model=deployment_name,
        messages=[
            {"role": "system", "content": _high_type_prompt},
            {"role": "user", "content": body}
        ],
        response_format={"type": "json_object"}
    )
    result = response.choices[0].message.content
    label = json.loads(result)["label"]
    return label

def generate_suggestions(history):
    history_str = json.dumps(history)
    prompt = (
        "You are an AI assistant helping with SMS replies. Based on this conversation history (alternating user and assistant messages, latest at end):\n"
        + history_str + "\n\nGenerate exactly 3 short, appropriate reply suggestions. Make them concise and natural.\n"
        "Return only JSON: {\"suggestions\": [\"suggestion1\", \"suggestion2\", \"suggestion3\"]}"
    )
    response = client.chat.completions.create(
        model=deployment_name,
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"}
    )
    result = response.choices[0].message.content
    suggestions = json.loads(result)["suggestions"]
    return suggestions

def generate_suggestions_async(history, room):
    try:
        suggestions = generate_suggestions(history)
        socketio.emit('ai_suggestions_ready', {'suggestions': suggestions}, room=room)
    except Exception as e:
        logger.error(f"Error generating suggestions: {str(e)}")
        socketio.emit('ai_error', {'error': 'Failed to generate suggestions'}, room=room)

def rewrite_message(message):
    prompt = (
        "Rewrite this SMS message to correct grammar, improve clarity, and make it more polite if appropriate. Keep it concise.\n"
        "Message: " + message + "\n\nReturn only JSON: {\"rewritten\": \"rewritten message\"}"
    )
    response = client.chat.completions.create(
        model=deployment_name,
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"}
    )
    result = response.choices[0].message.content
    rewritten = json.loads(result)["rewritten"]
    return rewritten

def rewrite_message_async(message, room):
    try:
        rewritten = rewrite_message(message)
        socketio.emit('ai_rewrite_ready', {'rewritten': rewritten}, room=room)
    except Exception as e:
        logger.error(f"Error rewriting message: {str(e)}")
        socketio.emit('ai_error', {'error': 'Failed to rewrite message'}, room=room)

def summarize_conversation(history):
    history_str = json.dumps(history)
    prompt = (
        "Summarize this SMS conversation briefly (2-3 sentences). Focus on key points, actions, and sentiment. History (alternating user/assistant, latest at end):\n"
        + history_str + "\n\nReturn only JSON: {\"summary\": \"your summary here\"}"
    )
    response = client.chat.completions.create(
        model=deployment_name,
        messages=[{"role": "system", "content": prompt}],
        response_format={"type": "json_object"}
    )
    result = response.choices[0].message.content
    summary = json.loads(result)["summary"]
    return summary