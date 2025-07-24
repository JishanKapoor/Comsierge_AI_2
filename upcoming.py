import os
from datetime import datetime, timedelta
import json
from openai import AzureOpenAI
import pytz
from bson import ObjectId
from db import store
import logging
import threading
import time
import re
from extensions import socketio

# Configure logging
logger = logging.getLogger(__name__)

# Set default TERM if unset to suppress errors
if "TERM" not in os.environ:
    os.environ["TERM"] = "xterm"

# Initialize Azure OpenAI client
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

def cleanup_past_events():
    """Remove events that are more than 4 hours in the past."""
    now = datetime.now(pytz.UTC)
    try:
        result = store.db.upcoming_events.delete_many({
            'proposed_time': {'$lt': now - timedelta(hours=4)}
        })
        logger.debug(f"Cleaned up {result.deleted_count} past events.")
    except Exception as e:
        logger.error(f"Error cleaning up past events: {str(e)}")

def generate_summary(intent, time, user_message, type):
    """Generate a concise summary for the event description."""
    if time:
        time_str = time.strftime('%Y-%m-%d %H:%M')
    else:
        time_str = 'unspecified time'
    prompt = f"Summarize this event as a short description (10-20 words) for a {intent} {type} at {time_str}. Base it on the message content: {user_message}"
    try:
        response = client.chat.completions.create(
            model=deployment_name,
            messages=[
                {"role": "system", "content": "Generate a concise summary for the event description."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=10000
        )
        raw_response = response.choices[0].message.content.strip()
        return raw_response
    except Exception as e:
        logger.error(f"Error generating summary: {str(e)}")
        return user_message[:50]  # Fallback to truncated message

def extract_time_and_intent(message: str, phone_id: str, from_number: str, to_number: str, direction: str) -> dict:
    """Analyze a message to detect meeting-related intents and extract meeting times."""
    for attempt in range(5):
        try:
            current_date = datetime.now(pytz.UTC).strftime("%Y-%m-%d")
            contact = store.db.contacts.find_one({
                'phone_id': ObjectId(phone_id),
                'phone_number': from_number if direction == 'received' else to_number
            })
            user_timezone = contact.get('timezone', 'UTC') if contact else 'UTC'
            response = client.chat.completions.create(
                model=deployment_name,
                messages=[
                    {
                        "role": "system",
                        "content": f"""
                            You are an assistant that analyzes messages to detect meeting-related intents and extract meeting times.
                            - Today's date is {current_date}.
                            - User timezone is {user_timezone}. Convert local times to UTC (e.g., 10 PM EDT = 22:00 UTC).
                            - The input includes the last 20 messages of conversation history followed by the current message, formatted as:
                              <timestamp> (sent/received): <message>
                              ...
                              Current message ({'received' if direction == 'received' else 'sent'}): <current_message>
                            - Use the conversation history to determine the context of the current message.
                            - For confirmation messages, set intent to "confirm" only if:
                              1. The history contains a clear meeting proposal (e.g., containing keywords like 'meet', 'meeting', 'schedule', 'at', 'tomorrow', 'Monday', or specific times/dates) within the last 20 messages.
                              2. The current message contains keywords like 'okay', 'ok', 'sure', 'ssure', 'agreed', 'yes', 'sounds good', 'let\'s do it', 'i will be there', 'cool', 'yup', 'locked in', 'see you', 'i\'m down', 'alright', 'confirmed', 'see you there'.
                              3. The meeting proposal is from the opposite direction (e.g., if current is (received), proposal must be (sent); if current is (sent), proposal must be (received)).
                              4. There is no recent cancellation (e.g., 'cancel', 'can\'t make it', 'no can\'t', 'cannot make it', 'lets cancel it', 'not happening', 'gotta postpone', 'change of plans', 'not free', 'not feeling well', 'naah man, can\'t', 'cancel it man') within the last 5 messages after the proposal.
                            - If the current message is from the same direction as the most recent proposal (e.g., both sent by the user), set intent to "none" even if it contains confirmation keywords.
                            - For cancellations, set intent to "cancel" if the message contains keywords like 'cancel', 'can\'t make it', 'no can\'t', 'cannot make it', 'lets cancel it', 'not happening', 'gotta postpone', 'change of plans', 'not free', 'not feeling well', 'naah man, can\'t', 'cancel it man'.
                            - If the message contains both cancellation and a new proposal (e.g., "can\'t make it, let\'s do tomorrow"), set intent to "reschedule", time to the new time, target_time to the referenced/canceled time.
                            - If the current message is short and contains only a time (e.g., "at 7?", "8 pm"), treat it as a rescheduling or confirmation of the most recent unconfirmed meeting proposal in the history or database. Use the date from the most recent proposal and apply the specified time. If no date is specified, assume today.
                            - Extract specific dates (e.g., "2 August" as "2025-08-02") or relative dates (e.g., "next Monday", "tomorrow").
                            - For "at X" or "today at X" (e.g., "at 9"), assume X is the hour in 24-hour format for today in user timezone, converted to UTC.
                              - If AM/PM is not specified and the current time is past X:00 AM, interpret as X:00 PM.
                              - If the time is in the past by more than 4 hours or not on the current day (unless specified), return null for time.
                            - For "at 12" without AM/PM, interpret as 12:00 (noon) if current time is before noon, or 00:00 the next day (midnight) if after noon.
                            - For "tomorrow at X", assume X is the hour for the next day.
                            - For specific dates like "2 August" or "next Monday", compute the correct date in 2025 or 2026.
                            - For vague times without specific hours, default to:
                              - morning: 09:00, afternoon: 14:00, evening: 18:00, night: 21:00
                              - later today: current time + 2 hours
                              - this weekend: next Saturday 12:00
                              - tomorrow: 12:00 if no time specified
                            - For vague rescheduling (e.g., "some other day"), assume the next day at 12:00.
                            - For special times like "midnight" (00:00), "noon" (12:00), interpret accordingly.
                            - For "next week", use exactly 7 days from today.
                            - For "next month", use exactly 30 days from today.
                            - For invalid times or dates (e.g., "3:30 pm", "32 august"), return time as null and intent as "propose" if meeting-related.
                            - For cancellations, if no specific time/date is referenced, set target_time to the most recent unconfirmed or confirmed meeting time in the history or database.
                            - Return a JSON string with:
                              - intent: one of "propose", "confirm", "reschedule", "cancel", "none"
                              - time: ISO format string (e.g., "2025-07-24T20:00:00Z") if a specific time is found, else null
                              - target_time: ISO format string if a specific existing time/date is referenced, else the most recent meeting time if intent is cancel, else null
                              - type: one of "meeting", "family_meeting", "appointment", "task", "deadline", "emergency", "other"
                            """
                    },
                    {"role": "user", "content": message}
                ],
                temperature=0.1
            )
            raw_response = response.choices[0].message.content
            logger.info(f"AI response for message: {raw_response}")
            try:
                result = json.loads(raw_response.strip("```json\n").rstrip("```"))
                if result.get("type") is None or result.get("type") == "null":
                    result["type"] = "other"
            except json.JSONDecodeError as e:
                logger.error(f"JSON parsing error: {e}, Raw response: {raw_response}")
                return {"intent": "none", "time": None, "target_time": None, "type": "other"}

            # Override confirmation and cancellation logic
            current_msg = message.split("Current message ")[-1].split("): ")[1] if "(" in message.split("Current message ")[1] else message.split("Current message: ")[-1].strip()
            current_msg_lower = current_msg.lower()
            history_msgs = [msg for msg in message.split("\n") if "(sent):" in msg or "(received):" in msg]
            meeting_keywords = ["meet", "meeting", "schedule", "at ", " pm", " am", "noon", "midnight", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            cancel_keywords = ["cancel", "can't make it", "no can't", "cannot make it", "lets cancel it", "not happening", "gotta postpone", "change of plans", "not free", "not feeling well", "naah man, can't", "cancel it man"]
            confirmation_keywords = ["okay", "ok", "sure", "ssure", "agreed", "yes", "sounds good", "let's do it", "i will be there", "cool", "yup", "locked in", "see you", "i'm down", "alright", "confirmed", "see you there"]

            # Check for cancellations first
            if any(keyword in current_msg_lower for keyword in cancel_keywords):
                recent_event = store.db.upcoming_events.find_one({
                    'phone_id': ObjectId(phone_id),
                    'status': {'$in': ['unconfirmed', 'confirmed', 'rescheduled']},
                    '$or': [
                        {'from_number': from_number, 'to_number': to_number},
                        {'from_number': to_number, 'to_number': from_number}
                    ],
                    'proposed_time': {'$gte': datetime.now(pytz.UTC) - timedelta(days=1)}
                }, sort=[('created_at', -1)])
                result = {
                    "intent": "cancel",
                    "time": None,
                    "target_time": recent_event.get('proposed_time').isoformat() if recent_event else None,
                    "type": "meeting"
                }
                logger.debug(f"Overriding AI response to cancel: {result}")

            # Check for confirmations
            elif any(keyword in current_msg_lower for keyword in confirmation_keywords):
                proposal_in_history = any(any(keyword in msg.lower() for keyword in meeting_keywords) for msg in history_msgs)
                proposal_direction_opposite = any(
                    ("(sent):" in msg if direction == "received" else "(received):" in msg)
                    and any(keyword in msg.lower() for keyword in meeting_keywords)
                    for msg in history_msgs
                )
                recent_cancellation = any(
                    any(keyword in msg.lower() for keyword in cancel_keywords)
                    for msg in history_msgs[-5:]  # Check last 5 messages for cancellations
                )
                logger.debug(f"Confirmation check: proposal_in_history={proposal_in_history}, direction_opposite={proposal_direction_opposite}, recent_cancellation={recent_cancellation}, history_msgs={history_msgs}")

                if proposal_in_history and proposal_direction_opposite and not recent_cancellation:
                    recent_event = store.db.upcoming_events.find_one({
                        'phone_id': ObjectId(phone_id),
                        'status': {'$in': ['unconfirmed', 'rescheduled']},
                        '$or': [
                            {'from_number': from_number, 'to_number': to_number},
                            {'from_number': to_number, 'to_number': from_number}
                        ],
                        'proposed_time': {'$gte': datetime.now(pytz.UTC) - timedelta(days=1)}
                    }, sort=[('created_at', -1)])
                    if recent_event:
                        result = {
                            "intent": "confirm",
                            "time": None,
                            "target_time": recent_event.get('proposed_time').isoformat() if recent_event.get('proposed_time') else None,
                            "type": "meeting"
                        }
                        logger.debug(f"Overriding AI response to confirm: {result}")
                    else:
                        logger.debug(f"No recent event found in DB for confirmation: phone_id={phone_id}, from_number={from_number}, to_number={to_number}")
                        result = {"intent": "none", "time": None, "target_time": None, "type": "other"}
                else:
                    logger.debug(f"Confirmation override failed: proposal_in_history={proposal_in_history}, direction_opposite={proposal_direction_opposite}, recent_cancellation={recent_cancellation}")
                    result = {"intent": "none", "time": None, "target_time": None, "type": "other"}

            # Handle follow-up proposals
            elif any(keyword in current_msg_lower for keyword in ["after this meeting", "meet again", "next meeting"]):
                recent_event = store.db.upcoming_events.find_one({
                    'phone_id': ObjectId(phone_id),
                    'status': {'$in': ['unconfirmed', 'confirmed', 'rescheduled']},
                    '$or': [
                        {'from_number': from_number, 'to_number': to_number},
                        {'from_number': to_number, 'to_number': from_number}
                    ],
                    'proposed_time': {'$gte': datetime.now(pytz.UTC) - timedelta(days=1)}
                }, sort=[('created_at', -1)])
                if recent_event and result["time"]:
                    result["intent"] = "propose"
                    logger.debug(f"Detected follow-up meeting proposal: {result}")

            now = datetime.now(pytz.UTC)
            current_day = now.date()
            if result["time"]:
                try:
                    result["time"] = datetime.fromisoformat(result["time"].replace("Z", "+00:00"))
                    if result["time"].date() < current_day or (result["time"] < now - timedelta(hours=4) and result["time"].date() == current_day):
                        logger.debug(f"Skipping past time: {result['time']}")
                        result["time"] = None
                except ValueError:
                    result["time"] = None
            if result.get("target_time"):
                try:
                    result["target_time"] = datetime.fromisoformat(result["target_time"].replace("Z", "+00:00"))
                except ValueError:
                    result["target_time"] = None
            return result
        except Exception as e:
            logger.error(f"AI processing error (attempt {attempt + 1}): {str(e)}")
            if attempt < 4:
                time.sleep(0.5 * (attempt + 1))
            continue

    # Fallback logic
    current_msg_lower = message.split("Current message ")[-1].split("): ")[1] if "(" in message.split("Current message ")[1] else message.split("Current message: ")[-1].strip().lower()
    meeting_keywords = ["meet", "meeting", "schedule", "at ", " pm", " am", "noon", "midnight", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "catch up", "hop on", "gym", "come over", "dinner", "zoom"]
    cancel_keywords = ["cancel", "can't make it", "no can't", "cannot make it", "lets cancel it", "not happening", "gotta postpone", "change of plans", "not free", "not feeling well", "naah man, can't", "cancel it man"]
    if any(keyword in current_msg_lower for keyword in meeting_keywords):
        recent_event = store.db.upcoming_events.find_one({
            'phone_id': ObjectId(phone_id),
            'status': {'$in': ['unconfirmed', 'confirmed', 'rescheduled']},
            '$or': [
                {'from_number': from_number, 'to_number': to_number},
                {'from_number': to_number, 'to_number': from_number}
            ],
            'proposed_time': {'$gte': datetime.now(pytz.UTC) - timedelta(days=1)}
        }, sort=[('created_at', -1)])
        return {
            "intent": "propose",
            "time": None,
            "target_time": recent_event.get('proposed_time').isoformat() if recent_event and any(keyword in current_msg_lower for keyword in ["after this meeting", "meet again", "next meeting"]) else None,
            "type": "meeting"
        }
    if any(keyword in current_msg_lower for keyword in cancel_keywords):
        recent_event = store.db.upcoming_events.find_one({
            'phone_id': ObjectId(phone_id),
            'status': {'$in': ['unconfirmed', 'confirmed', 'rescheduled']},
            '$or': [
                {'from_number': from_number, 'to_number': to_number},
                {'from_number': to_number, 'to_number': from_number}
            ],
            'proposed_time': {'$gte': datetime.now(pytz.UTC) - timedelta(days=1)}
        }, sort=[('created_at', -1)])
        return {
            "intent": "cancel",
            "time": None,
            "target_time": recent_event.get('proposed_time').isoformat() if recent_event else None,
            "type": "meeting"
        }
    if any(keyword in current_msg_lower for keyword in ["reschedule", "postpone", "move", "shift", "push", "delay", "late"]):
        return {
            "intent": "reschedule",
            "time": (datetime.now(pytz.UTC) + timedelta(days=1)).replace(hour=12, minute=0, second=0, tzinfo=pytz.UTC),
            "target_time": None,
            "type": "meeting"
        }
    return {"intent": "none", "time": None, "target_time": None, "type": "other"}

def update_upcoming(phone_id, message_id, intent, time, target_time, type, message_type, from_number, to_number):
    """Update upcoming events in MongoDB based on intent."""
    try:
        # Ensure no None group_ids in existing events
        store.db.upcoming_events.update_many({'group_id': None}, {'$set': {'group_id': ObjectId()}})

        now = datetime.now(pytz.UTC)
        alias = None
        contact = None
        if message_type == 'received':
            contact = store.db.contacts.find_one({
                'phone_id': ObjectId(phone_id),
                'phone_number': from_number
            })
            alias = contact['alias'] if contact else from_number
        else:  # sent
            contact = store.db.contacts.find_one({
                'phone_id': ObjectId(phone_id),
                'phone_number': to_number
            })
            alias = contact['alias'] if contact else to_number

        message_doc = store.db.message_log.find_one({'_id': ObjectId(message_id)})
        if not message_doc:
            logger.error(f"Message {message_id} not found for phone_id {phone_id}")
            return

        user_message = message_doc.get('body', '')
        if message_type == 'received':
            user_message = message_doc.get('body_translated', user_message)
        else:
            user_message = message_doc.get('original_body', user_message)

        if intent == "none":
            return

        # Match events based on phone numbers and status
        query = {
            'phone_id': ObjectId(phone_id),
            'status': {'$in': ['unconfirmed', 'confirmed', 'rescheduled']},
            '$or': [
                {'from_number': from_number, 'to_number': to_number},
                {'from_number': to_number, 'to_number': from_number}
            ],
            'proposed_time': {'$gte': now - timedelta(hours=4)}
        }
        if target_time:
            query['proposed_time'] = {
                '$gte': target_time - timedelta(hours=1),
                '$lte': target_time + timedelta(hours=1)
            }

        events = list(store.db.upcoming_events.find(query).sort('updated_at', -1).limit(1))
        logger.debug(f"Found {len(events)} matching events for intent {intent}: {events}")

        group_id = ObjectId()
        if events:
            group_id = events[0]['group_id']
            store.db.upcoming_events.update_many({
                'phone_id': ObjectId(phone_id),
                '$or': [
                    {'from_number': from_number, 'to_number': to_number},
                    {'from_number': to_number, 'to_number': from_number}
                ],
                'group_id': None
            }, {'$set': {'group_id': group_id}})

        room = f"user_{phone_id}"
        description = generate_summary(intent, time, user_message, type)

        if intent == "propose" and time and time >= now - timedelta(hours=4):
            event_data = {
                'phone_id': ObjectId(phone_id),
                'message_id': ObjectId(message_id),
                'proposed_time': time,
                'description': description,
                'type': type,
                'status': 'unconfirmed',
                'reason': None,
                'alias': alias,
                'from_number': from_number,
                'to_number': to_number,
                'created_at': now,
                'updated_at': now,
                'is_latest': True,
                'group_id': group_id
            }
            result = store.db.upcoming_events.insert_one(event_data)
            socketio.emit('new_event', {
                '_id': str(result.inserted_id),
                'proposed_time': time.isoformat() if time else None,
                'description': description,
                'type': type,
                'status': 'unconfirmed',
                'reason': None,
                'alias': alias,
                'is_latest': True,
                'group_id': str(group_id)
            }, room=room)
            logger.debug(f"Proposed new event: {description} for phone_id {phone_id}")

        elif intent == "confirm":
            if events:
                event = events[0]
                matched_time = time == event.get('proposed_time') if time else True
                if time and time >= now - timedelta(hours=4) and not matched_time:
                    reason = f"Rescheduled to {time.strftime('%Y-%m-%d %H:%M')}"
                    store.db.upcoming_events.update_one(
                        {'_id': event['_id']},
                        {'$set': {'status': 'rescheduled', 'reason': reason, 'updated_at': now, 'is_latest': False, 'group_id': group_id}}
                    )
                    socketio.emit('update_event', {
                        '_id': str(event['_id']),
                        'status': 'rescheduled',
                        'reason': reason,
                        'is_latest': False
                    }, room=room)
                    event_data = {
                        'phone_id': ObjectId(phone_id),
                        'message_id': ObjectId(message_id),
                        'proposed_time': time,
                        'description': description,
                        'type': type,
                        'status': 'confirmed',
                        'reason': None,
                        'alias': alias,
                        'from_number': from_number,
                        'to_number': to_number,
                        'created_at': now,
                        'updated_at': now,
                        'is_latest': True,
                        'group_id': group_id
                    }
                    result = store.db.upcoming_events.insert_one(event_data)
                    socketio.emit('new_event', {
                        '_id': str(result.inserted_id),
                        'proposed_time': time.isoformat() if time else None,
                        'description': description,
                        'type': type,
                        'status': 'confirmed',
                        'reason': None,
                        'alias': alias,
                        'is_latest': True,
                        'group_id': str(group_id)
                    }, room=room)
                    logger.debug(f"Rescheduled and confirmed event: {description} for phone_id {phone_id}")
                else:
                    store.db.upcoming_events.update_one(
                        {'_id': event['_id']},
                        {'$set': {'status': 'confirmed', 'reason': None, 'updated_at': now, 'is_latest': True, 'group_id': group_id, 'description': description}}
                    )
                    socketio.emit('update_event', {
                        '_id': str(event['_id']),
                        'status': 'confirmed',
                        'reason': None,
                        'is_latest': True,
                        'description': description
                    }, room=room)
                    logger.debug(f"Confirmed event: {description} for phone_id {phone_id}")
            else:
                logger.warning(f"No matching event found for confirmation: phone_id={phone_id}, from_number={from_number}, to_number={to_number}, target_time={target_time}")

        elif intent == "reschedule":
            if events:
                event = events[0]
                reason = f"Rescheduled to {(time.strftime('%Y-%m-%d %H:%M') if time else 'unspecified time')}"
                store.db.upcoming_events.update_one(
                    {'_id': event['_id']},
                    {'$set': {'status': 'rescheduled', 'reason': reason, 'updated_at': now, 'is_latest': False, 'group_id': group_id}}
                )
                socketio.emit('update_event', {
                    '_id': str(event['_id']),
                    'status': 'rescheduled',
                    'reason': reason,
                    'is_latest': False
                }, room=room)
            if time and time >= now - timedelta(hours=4):
                event_data = {
                    'phone_id': ObjectId(phone_id),
                    'message_id': ObjectId(message_id),
                    'proposed_time': time,
                    'description': description,
                    'type': type,
                    'status': 'unconfirmed',
                    'reason': None,
                    'alias': alias,
                    'from_number': from_number,
                    'to_number': to_number,
                    'created_at': now,
                    'updated_at': now,
                    'is_latest': True,
                    'group_id': group_id
                }
                result = store.db.upcoming_events.insert_one(event_data)
                socketio.emit('new_event', {
                    '_id': str(result.inserted_id),
                    'proposed_time': time.isoformat() if time else None,
                    'description': description,
                    'type': type,
                    'status': 'unconfirmed',
                    'reason': None,
                    'alias': alias,
                    'is_latest': True,
                    'group_id': str(group_id)
                }, room=room)
                logger.debug(f"Rescheduled event: {description} for phone_id {phone_id}")

        elif intent == "cancel":
            query = {
                'phone_id': ObjectId(phone_id),
                'status': {'$in': ['unconfirmed', 'confirmed', 'rescheduled']},
                '$or': [
                    {'from_number': from_number, 'to_number': to_number},
                    {'from_number': to_number, 'to_number': from_number}
                ],
                'proposed_time': {'$gte': now - timedelta(hours=4)}
            }
            if target_time:
                query['proposed_time'] = {
                    '$gte': target_time - timedelta(hours=1),
                    '$lte': target_time + timedelta(hours=1)
                }
            events = list(store.db.upcoming_events.find(query).sort('updated_at', -1).limit(1))
            if events:
                event = events[0]
                store.db.upcoming_events.update_one(
                    {'_id': event['_id']},
                    {'$set': {'status': 'canceled', 'reason': 'Canceled by user', 'updated_at': now, 'is_latest': True, 'group_id': group_id, 'description': description}}
                )
                socketio.emit('update_event', {
                    '_id': str(event['_id']),
                    'status': 'canceled',
                    'reason': 'Canceled by user',
                    'is_latest': True,
                    'description': description
                }, room=room)
                logger.debug(f"Canceled event: {description} for phone_id {phone_id}")
            else:
                logger.warning(f"No matching event found for cancellation: phone_id={phone_id}, from_number={from_number}, to_number={to_number}, target_time={target_time}")
    except Exception as e:
        logger.error(f"Error updating upcoming event: {str(e)}")

def analyze_for_event(phone_id, message_id, direction):
    """Analyze a message for potential events and update the database."""
    try:
        message = store.db.message_log.find_one({'_id': ObjectId(message_id), 'phone_id': ObjectId(phone_id)})
        if not message:
            logger.error(f"Message {message_id} not found for phone_id {phone_id}")
            return

        # Correct assignment: counterpart is the other party, user_number is the user's phone
        if direction == 'received':
            counterpart = message.get('from_number')
            user_number = message.get('to_number')
        else:  # sent
            counterpart = message.get('to_number')
            user_number = message.get('from_number')

        body = message.get('body_translated', message.get('body', ''))

        # Fetch the last 20 messages for context
        now = datetime.now(pytz.UTC)
        recent_messages = list(store.db.message_log.find({
            'phone_id': ObjectId(phone_id),
            '$or': [
                {'from_number': counterpart, 'to_number': user_number},
                {'from_number': user_number, 'to_number': counterpart}
            ],
            'timestamp': {'$gte': now - timedelta(days=1)}  # Last 24 hours to ensure proposal is included
        }).sort('timestamp', -1).limit(20))

        # Log the retrieved messages for debugging
        logger.debug(f"Retrieved {len(recent_messages)} recent messages for phone_id {phone_id}: {[msg.get('body', '') for msg in recent_messages]}")

        # Format conversation history, excluding the current message
        context = "\n".join([
            f"{msg['timestamp'].strftime('%Y-%m-%d %H:%M:%S')} ({'sent' if msg['direction'] == 'sent' else 'received'}): {msg.get('body_translated', msg.get('body', ''))}"
            for msg in recent_messages if str(msg['_id']) != str(message_id)
        ])
        input_message = f"Recent conversation:\n{context}\nCurrent message ({'received' if direction == 'received' else 'sent'}): {body}"

        # Log the input message for debugging
        logger.debug(f"Input message for AI: {input_message}")

        def process_event():
            try:
                result = extract_time_and_intent(input_message, phone_id, counterpart, user_number, direction)
                if result["intent"] != "none":
                    update_upcoming(
                        phone_id,
                        message_id,
                        result["intent"],
                        result["time"],
                        result.get("target_time"),
                        result["type"],
                        direction,
                        counterpart if direction == 'received' else user_number,
                        user_number if direction == 'received' else counterpart
                    )
            except Exception as e:
                logger.error(f"Error processing event for message {message_id}: {str(e)}")

        thread = threading.Thread(target=process_event)
        thread.start()
    except Exception as e:
        logger.error(f"Error analyzing event for message {message_id}: {str(e)}")

def get_upcoming_events(phone_id):
    """Retrieve upcoming events for a specific phone_id."""
    try:
        cleanup_past_events()
        events = list(store.db.upcoming_events.find({
            'phone_id': ObjectId(phone_id),
            'proposed_time': {'$gte': datetime.now(pytz.UTC) - timedelta(hours=4)}
        }).sort('proposed_time', 1))
        return events
    except Exception as e:
        logger.error(f"Error retrieving upcoming events for phone_id {phone_id}: {str(e)}")
        return []