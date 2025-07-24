window.addEventListener('load', () => {
    document.body.classList.add('loaded');
    document.querySelectorAll('.error-message').forEach(el => {
        el.style.display = 'none';
    });
    updateUTCTime();
    setInterval(updateUTCTime, 1000); // Update UTC time every second
});

function updateUTCTime() {
    const now = new Date();
    const options = { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' };
    document.getElementById('utc-time').textContent = now.toLocaleString('en-US', options) + ' UTC';
}

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    sidebar.classList.toggle('active');
    overlay.classList.toggle('hidden');
}

// Save Phone Number
document.getElementById('save-phone-number').addEventListener('click', () => {
    const phoneNumber = document.getElementById('phone-number').value.trim();
    if (!phoneNumber || !/^\+\d{10,15}$/.test(phoneNumber)) {
        document.getElementById('phone-number-error').style.display = 'block';
        return;
    }
    document.getElementById('phone-number-error').style.display = 'none';

    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': 'update_personal_phone',
            'phone_number': phoneNumber
        })
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Phone number saved successfully!' : data.error || 'Error saving phone number.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) location.reload();
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error saving phone number.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
});

// Save Sending Mode
document.getElementById('save-sending-mode').addEventListener('click', () => {
    const selectedMode = document.querySelector('input[name="sending-mode"]:checked');
    if (!selectedMode) {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Please select a sending mode.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        return;
    }
    const modeId = selectedMode.id;
    let mode;
    let duration = 'until_stopped';
    let startDate, startTime, endDate, endTime;
    let scheduleDiv;
    let errorId;
    if (modeId === 'high-medium-messages') {
        mode = 'high_medium';
        duration = 'until_stopped'; // Always until_stopped for high_medium
    } else if (modeId === 'high-messages') {
        mode = 'high';
        const durationRadio = document.querySelector('input[name="high-duration"]:checked');
        if (durationRadio && durationRadio.id === 'schedule-high') {
            duration = 'schedule';
            scheduleDiv = document.getElementById('high-schedule');
            errorId = 'high-time-error';
            startDate = document.getElementById('high-start-date').value;
            startTime = document.getElementById('high-start-time').value;
            endDate = document.getElementById('high-end-date').value;
            endTime = document.getElementById('high-end-time').value;
        }
    } else if (modeId === 'all-messages') {
        mode = 'all';
        const durationRadio = document.querySelector('input[name="all-duration"]:checked');
        if (durationRadio && durationRadio.id === 'schedule-all') {
            duration = 'schedule';
            scheduleDiv = document.getElementById('all-schedule');
            errorId = 'all-time-error';
            startDate = document.getElementById('all-start-date').value;
            startTime = document.getElementById('all-start-time').value;
            endDate = document.getElementById('all-end-date').value;
            endTime = document.getElementById('all-end-time').value;
        }
    } else if (modeId === 'no-messages') {
        mode = 'dnd';
        const durationRadio = document.querySelector('input[name="dnd-duration"]:checked');
        if (durationRadio && durationRadio.id === 'schedule-dnd') {
            duration = 'schedule';
            scheduleDiv = document.getElementById('dnd-schedule');
            errorId = 'dnd-time-error';
            startDate = document.getElementById('dnd-start-date').value;
            startTime = document.getElementById('dnd-start-time').value;
            endDate = document.getElementById('dnd-end-date').value;
            endTime = document.getElementById('dnd-end-time').value;
        }
    }

    let body = `action=update_sending_mode&mode=${mode}&duration=${duration}`;
    if (duration === 'schedule') {
        if (!startDate || !startTime || !endDate || !endTime) {
            document.getElementById(errorId).style.display = 'block';
            return;
        }
        const start = `${startDate}T${startTime}:00Z`;
        const end = `${endDate}T${endTime}:00Z`;
        const startDateObj = new Date(start);
        const endDateObj = new Date(end);
        if (isNaN(startDateObj) || isNaN(endDateObj) || endDateObj <= startDateObj) {
            document.getElementById(errorId).style.display = 'block';
            return;
        }
        body += `&start_date=${startDate}&start_time=${startTime}&end_date=${endDate}&end_time=${endTime}`;
    } else {
        // Hide time errors when not schedule
        ['high-time-error', 'all-time-error', 'dnd-time-error'].forEach(id => {
            document.getElementById(id).style.display = 'none';
        });
    }

    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: body
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Sending mode saved successfully!' : data.error || 'Error saving sending mode.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) location.reload();
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error saving sending mode.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
});

// Handle Sending Mode and Schedule Options
const sendingModes = document.getElementsByName('sending-mode');
sendingModes.forEach(mode => {
    mode.addEventListener('change', () => {
        document.getElementById('high-messages-options').classList.toggle('hidden', mode.id !== 'high-messages');
        document.getElementById('all-messages-options').classList.toggle('hidden', mode.id !== 'all-messages');
        document.getElementById('no-messages-options').classList.toggle('hidden', mode.id !== 'no-messages');
        document.getElementById('high-schedule').classList.add('hidden');
        document.getElementById('all-schedule').classList.add('hidden');
        document.getElementById('dnd-schedule').classList.add('hidden');
        ['high-time-error', 'all-time-error', 'dnd-time-error'].forEach(id => {
            document.getElementById(id).style.display = 'none';
        });
        if (mode.id === 'high-messages' && document.getElementById('schedule-high').checked) {
            document.getElementById('high-schedule').classList.remove('hidden');
        }
        if (mode.id === 'all-messages' && document.getElementById('schedule-all').checked) {
            document.getElementById('all-schedule').classList.remove('hidden');
        }
        if (mode.id === 'no-messages' && document.getElementById('schedule-dnd').checked) {
            document.getElementById('dnd-schedule').classList.remove('hidden');
        }
    });
});

document.getElementById('schedule-high').addEventListener('change', () => {
    document.getElementById('high-schedule').classList.toggle('hidden', !document.getElementById('schedule-high').checked);
    if (!document.getElementById('schedule-high').checked) document.getElementById('high-time-error').style.display = 'none';
});

document.getElementById('schedule-all').addEventListener('change', () => {
    document.getElementById('all-schedule').classList.toggle('hidden', !document.getElementById('schedule-all').checked);
    if (!document.getElementById('schedule-all').checked) document.getElementById('all-time-error').style.display = 'none';
});

document.getElementById('schedule-dnd').addEventListener('change', () => {
    document.getElementById('dnd-schedule').classList.toggle('hidden', !document.getElementById('schedule-dnd').checked);
    if (!document.getElementById('schedule-dnd').checked) document.getElementById('dnd-time-error').style.display = 'none';
});

// Add Bubble Function
function addBubble(inputId, listId, action) {
    const input = document.getElementById(inputId);
    const value = input.value.trim();
    if (!value) {
        document.getElementById(`${inputId}-error`).style.display = 'block';
        return;
    }
    if (inputId.includes('from') && !/^\+\d{10,15}$/.test(value) && !/^[a-zA-Z\s]+$/.test(value)) {
        document.getElementById(`${inputId}-error`).style.display = 'block';
        document.getElementById(`${inputId}-error`).textContent = 'Enter a valid phone number (+ followed by 10-15 digits) or contact name.';
        return;
    }
    if (inputId.includes('keywords') && !/^[a-zA-Z0-9\s,]+$/.test(value)) {
        document.getElementById(`${inputId}-error`).style.display = 'block';
        document.getElementById(`${inputId}-error`).textContent = 'Enter valid keywords (letters, numbers, spaces, or commas).';
        return;
    }
    document.getElementById(`${inputId}-error`).style.display = 'none';

    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': action,
            'value': value
        })
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Added successfully!' : data.error || 'Error adding item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) {
            input.value = ''; // Clear input after successful addition
            location.reload();
        }
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error adding item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
}

// Remove Bubble Functions
function removePriorityFrom(value) {
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': 'remove_priority_from',
            'value': value
        })
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Removed successfully!' : data.error || 'Error removing item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) location.reload();
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error removing item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
}

function removePriorityKeyword(value) {
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': 'remove_priority_keyword',
            'value': value
        })
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Removed successfully!' : data.error || 'Error removing item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) location.reload();
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error removing item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
}

function removeSpamIgnoreFrom(value) {
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': 'remove_spam_ignore_from',
            'value': value
        })
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Removed successfully!' : data.error || 'Error removing item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) location.reload();
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error removing item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
}

function removeSpamKeyword(value) {
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': 'remove_spam_keyword',
            'value': value
        })
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Removed successfully!' : data.error || 'Error removing item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) location.reload();
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error removing item.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
}

// Handle Enter for adding
document.getElementById('priority-from').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        addBubble('priority-from', 'priority-from-list', 'add_priority_from');
    }
});

document.getElementById('priority-keywords').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        addBubble('priority-keywords', 'priority-keywords-list', 'add_priority_keyword');
    }
});

document.getElementById('spam-from').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        addBubble('spam-from', 'spam-from-list', 'add_spam_ignore_from');
    }
});

document.getElementById('spam-keywords').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        addBubble('spam-keywords', 'spam-keywords-list', 'add_spam_keyword');
    }
});

// Update Languages
document.getElementById('receive-language').addEventListener('change', () => {
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': 'update_receive_language',
            'language': document.getElementById('receive-language').value
        })
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Receive language saved successfully!' : data.error || 'Error saving receive language.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) location.reload();
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error saving receive language.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
});

document.getElementById('send-language').addEventListener('change', () => {
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': 'update_send_language',
            'language': document.getElementById('send-language').value
        })
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Send language saved successfully!' : data.error || 'Error saving send language.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) location.reload();
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error saving send language.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
});

// Cancel Upcoming Mode
document.getElementById('cancel-upcoming')?.addEventListener('click', () => {
    fetch('/settings', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': 'cancel_upcoming'
        })
    })
    .then(response => response.json())
    .then(data => {
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-red-500', 'bg-green-500');
        banner.classList.add(data.success ? 'bg-green-500' : 'bg-red-500');
        banner.textContent = data.success ? 'Upcoming mode canceled successfully!' : data.error || 'Error canceling upcoming mode.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
        if (data.success) location.reload();
    })
    .catch(error => {
        console.error('Error:', error);
        const banner = document.getElementById('notification-banner');
        banner.classList.remove('bg-green-500');
        banner.classList.add('bg-red-500');
        banner.textContent = 'Error canceling upcoming mode.';
        banner.style.display = 'block';
        setTimeout(() => {
            banner.style.display = 'none';
        }, 3000);
    });
});
