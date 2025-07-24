from flask import flash, redirect, url_for, session
from bson.objectid import ObjectId
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from db import store
import logging
from routes import scheduler
from datetime import datetime
import pytz
from extensions import socketio  # Import socketio if not already imported

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def unassign_phone_number(user_id):
    """Unassign a phone number from a user, keeping it available for reassignment."""
    try:
        user = store.db.users.find_one({'_id': ObjectId(user_id)})
        if not user or not user.get('selected_phone_id'):
            flash('User not found or no phone number assigned.', 'error')
            logger.error(f"Unassign failed: User {user_id} not found or no phone number assigned")
            return False

        phone_id = user['selected_phone_id']
        phone_doc = store.db.phone_numbers.find_one({'_id': ObjectId(phone_id), 'active': True})
        if not phone_doc:
            flash('Phone number not found or already inactive.', 'error')
            logger.error(f"Unassign failed: Phone {phone_id} not found or inactive")
            return False

        # Update user and phone number in database
        store.db.users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'selected_phone_id': None}}
        )
        store.db.phone_numbers.update_one(
            {'_id': ObjectId(phone_id)},
            {'$set': {'user_username': None, 'active': True}}
        )

        # Clean up associated data
        store.db.message_log.delete_many({'phone_id': ObjectId(phone_id)})
        store.db.contacts.delete_many({'phone_id': ObjectId(phone_id)})
        store.db.forwarding_rules.delete_many({'phone_id': ObjectId(phone_id)})
        # Explicit deletion for upcoming_events (all events, past or future)
        result = store.db.upcoming_events.delete_many({'phone_id': ObjectId(phone_id)})
        logger.info(f"Deleted {result.deleted_count} upcoming events for phone_id {phone_id}")

        # Cancel and remove scheduled messages
        scheduled_messages = store.db.scheduled_messages.find({'phone_id': ObjectId(phone_id), 'status': 'pending'})
        for msg in scheduled_messages:
            store.db.scheduled_messages.update_one(
                {'_id': msg['_id']},
                {'$set': {'status': 'cancelled', 'cancelled_at': datetime.now(pytz.UTC),
                          'updated_at': datetime.now(pytz.UTC)}}
            )
            try:
                scheduler.remove_job(f"send_{str(msg['_id'])}")
                logger.info(f"Removed scheduled job send_{str(msg['_id'])}")
            except:
                logger.debug(f"Job send_{str(msg['_id'])} already removed")

        store.db.scheduled_messages.delete_many({'phone_id': ObjectId(phone_id)})

        # Optional: Emit SocketIO event to clear UI for any connected clients (prevents stale data display)
        socketio.emit('clear_events', room=f"user_{phone_id}")

        flash('Phone number unassigned and associated data deleted successfully.', 'success')
        logger.info(f"Unassigned phone number {phone_doc['number']} from user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Error unassigning phone number for user {user_id}: {str(e)}", exc_info=True)
        flash(f'Error unassigning phone number: {str(e)}', 'error')
        return False


def delete_user(user_id):
    """Delete a user and all associated data, including unassigning phone number."""
    try:
        user = store.db.users.find_one({'_id': ObjectId(user_id)})
        if not user:
            flash('User not found.', 'error')
            logger.error(f"Delete failed: User {user_id} not found")
            return False

        if user.get('is_admin'):
            flash('Cannot delete admin user.', 'error')
            logger.warning(f"Attempted to delete admin user {user_id}")
            return False

        # Unassign phone number if assigned
        if user.get('selected_phone_id'):
            if not unassign_phone_number(user_id):
                return False

        # Delete user
        store.db.users.delete_one({'_id': ObjectId(user_id)})
        flash('User and all associated data deleted successfully.', 'success')
        logger.info(f"Deleted user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {str(e)}", exc_info=True)
        flash(f'Error deleting user: {str(e)}', 'error')
        return False
