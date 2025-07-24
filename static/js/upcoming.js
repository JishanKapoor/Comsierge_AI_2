document.addEventListener('DOMContentLoaded', () => {
    let socket;
    try {
        socket = io({
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionAttempts: Infinity,
            reconnectionDelay: 1000
        });
        console.log('Socket.IO initialized for room: user_' + phoneId);
    } catch (error) {
        console.error('Socket.IO initialization failed:', error);
        showNotification('Real-time updates unavailable. Refresh for changes.');
    }

    // Real-time event updates
    if (socket) {
        socket.on('connect', () => {
            console.log('Socket.IO connected');
            socket.emit('join_room', `user_${phoneId}`);
            // Start heartbeat to keep connection alive
            setInterval(() => {
                socket.emit('heartbeat', {});
            }, 30000); // Every 30 seconds
        });

        socket.on('disconnect', () => {
            console.log('Socket.IO disconnected');
        });

        socket.on('new_event', (event) => {
            console.log('Received new_event:', event);
            addOrUpdateEvent(event, 'new');
            sortGroups();
            filterEvents();
        });

        socket.on('update_event', (event) => {
            console.log('Received update_event:', event);
            addOrUpdateEvent(event, 'update');
            sortGroups();
            filterEvents();
        });

        socket.on('event_deleted', (data) => {
            console.log('Received event_deleted:', data);
            removeEvent(data.event_id);
            sortGroups();
            filterEvents();
        });

        socket.on('group_deleted', (data) => {
            console.log('Received group_deleted:', data);
            const group = document.querySelector(`.group-item[data-group-id="${data.group_id}"]`);
            if (group) {
                group.remove();
            }
            sortGroups();
            filterEvents();
            showNotification('Event group deleted successfully.');
        });

        socket.on('all_events_deleted', (data) => {
            console.log('Received all_events_deleted:', data);
            document.getElementById('event-list').innerHTML = '<p id="no-events-msg" class="text-xs text-zinc-400 text-center">No upcoming events found.</p>';
            showNotification(`${data.count} events deleted successfully.`);
        });
    }

    function addOrUpdateEvent(eventData, mode) {
        const groupId = eventData.group_id;
        let existingGroup = document.querySelector(`.group-item[data-group-id="${groupId}"]`);

        if (mode === 'update') {
            const existingItem = document.querySelector(`.event-item[data-event-id="${eventData._id}"]`);
            if (!existingItem) return;

            // Update fields in place
            if ('status' in eventData) {
                existingItem.dataset.status = eventData.status.toLowerCase();
                const statusP = existingItem.querySelector('p.font-medium.text-xs');
                if (statusP) {
                    statusP.textContent = `Status: ${eventData.status.charAt(0).toUpperCase() + eventData.status.slice(1)}` + (eventData.reason ? ` (${eventData.reason})` : '');
                    statusP.style.color = eventData.status === 'canceled' ? '#EF4444' : eventData.status === 'rescheduled' ? '#FBBF24' : '#10B981';
                }
                existingItem.style.borderLeft = `4px solid ${eventData.status === 'canceled' ? '#EF4444' : eventData.status === 'rescheduled' ? '#FBBF24' : '#10B981'}`;
            }
            if ('description' in eventData) {
                const title = existingItem.querySelector('p.font-semibold');
                if (title) {
                    const alias = title.textContent.split(': ')[0];
                    title.childNodes[0].textContent = `${alias}: ${eventData.description}`;
                }
                existingItem.dataset.description = eventData.description;
            }
            if ('proposed_time' in eventData) {
                existingItem.dataset.timestamp = eventData.proposed_time || '';
                const timeP = existingItem.querySelector('p.text-xs.text-zinc-400.mt-1');
                if (timeP) {
                    timeP.textContent = eventData.proposed_time ? new Date(eventData.proposed_time).toLocaleString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        year: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                        hour12: false
                    }) + ' UTC' : 'Time not specified';
                }
            }

            // Handle is_latest change
            if ('is_latest' in eventData) {
                const wasLatest = existingItem.classList.contains('latest');
                existingItem.dataset.latest = eventData.is_latest ? 'true' : 'false';
                const group = existingItem.closest('.group-item');
                const historyDiv = group.querySelector('.history');

                if (eventData.is_latest && !wasLatest) {
                    // Move from history to latest
                    historyDiv.removeChild(existingItem);
                    existingItem.classList.add('latest');
                    existingItem.addEventListener('click', toggleHistory);
                    if (!existingItem.querySelector('button.text-red-400')) {
                        const deleteBtn = document.createElement('button');
                        deleteBtn.type = 'button';
                        deleteBtn.className = 'text-red-400 hover:text-red-600 transition-colors';
                        deleteBtn.textContent = '🗑️';
                        deleteBtn.onclick = () => deleteGroup(existingItem.dataset.groupId);
                        existingItem.appendChild(deleteBtn);
                    }
                    group.insertBefore(existingItem, historyDiv);
                    updateGroupDatasets(group, existingItem);
                } else if (!eventData.is_latest && wasLatest) {
                    // Move from latest to history (insert at top)
                    existingItem.classList.remove('latest');
                    existingItem.removeEventListener('click', toggleHistory);
                    const deleteBtn = existingItem.querySelector('button.text-red-400');
                    if (deleteBtn) deleteBtn.remove();
                    historyDiv.insertBefore(existingItem, historyDiv.firstChild);
                }
            }

            return;
        }

        // For new events
        if (!existingGroup) {
            existingGroup = createGroup(groupId);
            document.getElementById('event-list').insertBefore(existingGroup, document.getElementById('event-list').firstChild);
        }
        const item = createEventElement(eventData);
        item.dataset.groupId = eventData.group_id;
        const historyDiv = existingGroup.querySelector('.history');

        if (eventData.is_latest) {
            const oldLatest = existingGroup.querySelector('.event-item.latest');
            if (oldLatest) {
                oldLatest.classList.remove('latest');
                oldLatest.removeEventListener('click', toggleHistory);
                const oldDeleteBtn = oldLatest.querySelector('button.text-red-400');
                if (oldDeleteBtn) oldDeleteBtn.remove();
                historyDiv.insertBefore(oldLatest, historyDiv.firstChild);
            }
            item.classList.add('latest');
            item.addEventListener('click', toggleHistory);
            existingGroup.insertBefore(item, historyDiv);
            updateGroupDatasets(existingGroup, item);
        } else {
            historyDiv.insertBefore(item, historyDiv.firstChild);
        }
    }

    function createGroup(groupId) {
        const groupDiv = document.createElement('div');
        groupDiv.className = 'group-item mb-4';
        groupDiv.dataset.groupId = groupId;
        const historyDiv = document.createElement('div');
        historyDiv.className = 'history hidden ml-4 space-y-2 border-l-2 border-zinc-700 pl-4';
        groupDiv.appendChild(historyDiv);
        return groupDiv;
    }

    function updateGroupDatasets(group, latest) {
        group.dataset.timestamp = latest.dataset.timestamp || '';
    }

    function toggleHistory() {
        this.nextSibling.classList.toggle('hidden');
    }

    function removeEvent(eventId) {
        const eventItem = document.querySelector(`.event-item[data-event-id="${eventId}"]`);
        if (eventItem) {
            const group = eventItem.closest('.group-item');
            const isLatest = eventItem.classList.contains('latest');
            eventItem.remove();
            if (isLatest) {
                const historyDiv = group.querySelector('.history');
                if (historyDiv.firstChild) {
                    const newLatest = historyDiv.firstChild;
                    historyDiv.removeChild(newLatest);
                    newLatest.classList.add('latest');
                    newLatest.addEventListener('click', toggleHistory);
                    const deleteBtn = document.createElement('button');
                    deleteBtn.type = 'button';
                    deleteBtn.className = 'text-red-400 hover:text-red-600 transition-colors';
                    deleteBtn.textContent = '🗑️';
                    deleteBtn.onclick = () => deleteGroup(newLatest.dataset.groupId);
                    newLatest.appendChild(deleteBtn);
                    group.insertBefore(newLatest, historyDiv);
                    updateGroupDatasets(group, newLatest);
                } else {
                    group.remove();
                }
            }
        }
    }

    function showNotification(message) {
        const banner = document.getElementById('notification-banner');
        banner.textContent = message;
        banner.classList.remove('hidden');
        banner.classList.add('bg-green-600');
        setTimeout(() => {
            banner.classList.add('hidden');
            banner.classList.remove('bg-green-600');
        }, 3000);
    }

    window.deleteEvent = function(eventId) {
        const eventItem = document.querySelector(`.event-item[data-event-id="${eventId}"]`);
        if (eventItem) {
            deleteGroup(eventItem.dataset.groupId);
        }
    };

    window.deleteGroup = function(groupId) {
        if (!groupId || groupId === 'None' || groupId === 'null') {
            showNotification('Cannot delete: Invalid group ID.');
            return;
        }
        if (confirm('Are you sure you want to delete this entire event group?')) {
            fetch(`/delete_group/${groupId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification(`${data.deleted_count} events deleted successfully.`);
                } else {
                    showNotification('Error deleting event group: ' + data.error);
                }
            })
            .catch(error => {
                console.error('Error deleting event group:', error);
                showNotification('Error deleting event group.');
            });
        }
    };

    window.deleteAllEvents = function() {
        if (confirm('Are you sure you want to delete all events?')) {
            fetch('/delete_all_events', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification(`${data.deleted_count} events deleted successfully.`);
                } else {
                    showNotification('Error deleting all events: ' + data.error);
                }
            })
            .catch(error => {
                console.error('Error deleting all events:', error);
                showNotification('Error deleting all events.');
            });
        }
    };

    window.filterEvents = function() {
        console.log('Filtering events...');
        const timeFilter = document.getElementById('filter-time-select').value;
        const groupItems = document.getElementsByClassName('group-item');
        const now = new Date();
        let visibleCount = 0;

        Array.from(groupItems).forEach(item => {
            const timestamp = item.dataset.timestamp ? new Date(item.dataset.timestamp) : null;
            const latest = item.querySelector('.event-item.latest');
            const isCanceled = latest && latest.dataset.status === 'canceled';
            let timeMatch = true;

            if (timestamp && !isNaN(timestamp)) {
                const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
                const tomorrow = new Date(today);
                tomorrow.setDate(today.getDate() + 1);
                const nextWeek = new Date(today);
                nextWeek.setDate(today.getDate() + 7);
                const nextMonth = new Date(today);
                nextMonth.setDate(today.getDate() + 30);

                if (timeFilter === 'today') {
                    timeMatch = timestamp.toDateString() === today.toDateString();
                } else if (timeFilter === 'tomorrow') {
                    timeMatch = timestamp.toDateString() === tomorrow.toDateString();
                } else if (timeFilter === 'next-week') {
                    timeMatch = timestamp >= today && timestamp < nextWeek;
                } else if (timeFilter === 'next-month') {
                    timeMatch = timestamp >= today && timestamp < nextMonth;
                } else if (timeFilter === 'later') {
                    timeMatch = timestamp >= nextMonth;
                }
            } else if (timeFilter !== 'all') {
                timeMatch = false;
            }

            if (timeMatch && (timeFilter === 'all' || !isCanceled)) {
                item.style.display = 'block';
                visibleCount++;
            } else {
                item.style.display = 'none';
            }
        });

        const noEventsMsg = document.getElementById('no-events-msg');
        if (visibleCount === 0) {
            noEventsMsg.classList.remove('hidden');
        } else {
            noEventsMsg.classList.add('hidden');
        }
        console.log(`Filtered ${visibleCount} events`);
    };

    function createEventElement(event) {
        console.log('Creating event element:', event);
        const div = document.createElement('div');
        div.className = 'event-item flex justify-between items-start p-3 rounded-md hover:bg-zinc-800 transition-colors';
        div.dataset.timestamp = event.proposed_time || '';
        div.dataset.eventId = event._id;
        div.dataset.sender = event.alias;
        div.dataset.description = event.description;
        div.dataset.status = event.status.toLowerCase();
        div.dataset.latest = event.is_latest ? 'true' : 'false';
        div.style.borderLeft = `4px solid ${event.status === 'canceled' ? '#EF4444' : event.status === 'rescheduled' ? '#FBBF24' : '#10B981'}`;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'event-content flex-1';

        const title = document.createElement('p');
        title.className = 'font-semibold text-white text-sm';
        title.textContent = `${event.alias}: ${event.description}`;
        if (event.is_latest) {
            const arrowSpan = document.createElement('span');
            arrowSpan.className = 'ml-2 text-zinc-400';
            arrowSpan.textContent = '▼';
            title.appendChild(arrowSpan);
        }

        const statusP = document.createElement('p');
        statusP.className = 'text-xs font-medium';
        statusP.style.color = event.status === 'canceled' ? '#EF4444' : event.status === 'rescheduled' ? '#FBBF24' : '#10B981';
        statusP.textContent = `Status: ${event.status.charAt(0).toUpperCase() + event.status.slice(1)}` + (event.reason ? ` (${event.reason})` : '');

        const timeP = document.createElement('p');
        timeP.className = 'text-xs text-zinc-400 mt-1';
        timeP.textContent = event.proposed_time ? new Date(event.proposed_time).toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
        }) + ' UTC' : 'Time not specified';

        contentDiv.appendChild(title);
        contentDiv.appendChild(statusP);
        contentDiv.appendChild(timeP);

        div.appendChild(contentDiv);

        if (event.is_latest) {
            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'text-red-400 hover:text-red-600 transition-colors';
            deleteBtn.textContent = '🗑️';
            deleteBtn.onclick = () => deleteGroup(event.group_id);
            div.appendChild(deleteBtn);
        }

        return div;
    }

    // Group initial events
    const eventList = document.getElementById('event-list');
    const initialItems = Array.from(eventList.getElementsByClassName('event-item'));
    eventList.innerHTML = '';
    const initialGroups = {};
    initialItems.forEach(item => {
        const groupId = item.dataset.groupId;
        if (!initialGroups[groupId]) initialGroups[groupId] = [];
        initialGroups[groupId].push(item);
    });
    Object.keys(initialGroups).forEach(groupId => {
        const groupEvents = initialGroups[groupId].sort((a, b) => {
            const timeA = a.dataset.timestamp ? new Date(a.dataset.timestamp) : new Date(0);
            const timeB = b.dataset.timestamp ? new Date(b.dataset.timestamp) : new Date(0);
            return timeB - timeA; // Latest first
        });
        let latest = groupEvents.find(e => e.dataset.latest === 'true');
        if (!latest) latest = groupEvents[0];
        const history = groupEvents.filter(e => e !== latest).sort((a, b) => {
            const timeA = a.dataset.timestamp ? new Date(a.dataset.timestamp) : new Date(0);
            const timeB = b.dataset.timestamp ? new Date(b.dataset.timestamp) : new Date(0);
            return timeB - timeA; // Latest first
        });
        const groupDiv = createGroup(groupId);
        latest.classList.add('latest');
        latest.addEventListener('click', toggleHistory);
        groupDiv.insertBefore(latest, groupDiv.firstChild);
        const historyDiv = groupDiv.querySelector('.history');
        history.forEach(h => {
            h.querySelector('button.text-red-400')?.remove();
            historyDiv.appendChild(h);
        });
        updateGroupDatasets(groupDiv, latest);
        eventList.insertBefore(groupDiv, eventList.firstChild);
    });

    // Initial sorting
    sortGroups();
    filterEvents();
});
