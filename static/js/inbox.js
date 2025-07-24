const socket = io();
const selectedPhoneId = window.selectedPhoneId;

let conversations = window.conversations || [];
let groupedMessages = window.groupedMessages || {};
let currentCounterpart = null;
let lastSentMessage = null; // Track last sent message to prevent duplicates

const contactList = document.getElementById('contactList');
const conversation = document.getElementById('conversation');
const conversationHeader = document.getElementById('conversation-header');
const conversationTitle = document.getElementById('conversation-title');
const conversationPhone = document.getElementById('conversation-phone');
const conversationCount = document.getElementById('conversation-count');
const chatMessages = document.getElementById('chatMessages');
const messageInput = document.getElementById('messageInput');
const messageInputContainer = document.getElementById('message-input-container');
const sendMessageBtn = document.getElementById('sendMessageBtn');
const searchInput = document.getElementById('searchInput');
const refreshInbox = document.getElementById('refresh-inbox');
const addContactBtn = document.getElementById('add-contact-btn');
const deleteChatBtn = document.getElementById('delete-chat-btn');
const aiSlider = document.getElementById('aiSlider');
const backdrop = document.getElementById('backdrop');
const closeSliderBtn = document.getElementById('closeSliderBtn');
const aiControlsBtn = document.getElementById('ai-controls-toggle');
const aiSummaryBtn = document.getElementById('ai-summary-toggle');
const tabs = document.querySelectorAll('.tab-btn');
const tabContents = document.querySelectorAll('.tab-content');
const suggestionsContainer = document.getElementById('suggestions-container');
const regenerateSuggestionsBtn = document.getElementById('regenerate-suggestions-btn');
const summaryText = document.getElementById('summary-text');
const summaryLoader = document.getElementById('summary-loader');

window.addEventListener('load', () => {
  document.body.classList.add('loaded');
  renderContacts();
  const initialConversation = conversations.find(c => c.active);
  if (initialConversation) {
    loadInbox(initialConversation.counterpart);
  } else {
    chatMessages.innerHTML = '<div class="text-center text-zinc-500 h-full flex items-center justify-center">Select a conversation to start chatting.</div>';
    messageInputContainer.style.display = 'none';
    conversationHeader.style.display = 'none';
  }
});

messageInput.addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = (Math.min(this.scrollHeight, 120)) + 'px';
});

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  if (sidebar && overlay) {
    sidebar.classList.toggle('active');
    overlay.classList.toggle('hidden');
  } else {
    console.error('Sidebar or overlay not found');
  }
}

function showModal(modalId, phone = '', alias = '') {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.remove('hidden');
    backdrop.classList.remove('hidden');
    if (modalId === 'addContactModal') {
      document.getElementById('addContactForm').reset();
      document.getElementById('contactPhone').value = phone;
      document.getElementById('contactName').value = alias;
    }
  }
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.add('hidden');
    backdrop.classList.add('hidden');
    if (modalId === 'addContactModal') {
      document.getElementById('addContactForm').reset();
      document.getElementById('contactPhoneError').classList.add('hidden');
      document.getElementById('contactNameError').classList.add('hidden');
    }
  }
}

function formatDate(iso) {
  if (!iso) return 'Never';
  const date = new Date(iso);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  return date.toLocaleDateString();
}

function getAvatarColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  const colors = ['6366F1', '10B981', 'F59E0B', 'EF4444', '8B5CF6'];
  return colors[Math.abs(hash) % colors.length];
}

function createContactItem(conv) {
  const item = document.createElement('div');
  item.classList.add('contact-item', 'flex', 'items-center', 'p-3', 'cursor-pointer', 'rounded-lg', 'transition-colors', conv.active ? 'bg-zinc-800' : 'hover:bg-zinc-800');
  item.dataset.counterpart = conv.counterpart;
  const avatarColor = getAvatarColor(conv.alias || conv.counterpart);
  item.innerHTML = `
    <div class="relative flex-shrink-0">
      <img class="h-10 w-10 rounded-full object-cover" src="${conv.avatar || 'https://placehold.co/100x100/' + avatarColor + '/FFFFFF?text=' + (conv.alias || conv.counterpart).substring(0, 2)}" alt="${conv.alias || conv.counterpart}">
      ${conv.highPriority ? '<div class="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-neon-orange shadow-lg shadow-neon-orange/50 border-2 border-[#1a1a1d]"></div>' : ''}
    </div>
    <div class="ml-4 flex-1 overflow-hidden">
      <p class="font-semibold text-sm text-white truncate">${conv.alias || conv.counterpart}</p>
      <p class="text-xs text-zinc-400 truncate">${conv.last_message || ''}</p>
    </div>
    <div class="flex flex-col items-end text-xs text-zinc-500 ml-2">
      <span>${formatDate(conv.last_timestamp)}</span>
      ${conv.unread > 0 ? `<span class="mt-1 flex items-center justify-center bg-blue-500 text-white h-5 w-5 rounded-full font-bold text-xs">${conv.unread}</span>` : ''}
    </div>
  `;
  item.addEventListener('click', () => loadInbox(conv.counterpart));
  return item;
}

function renderContacts() {
  contactList.innerHTML = '';
  conversations.forEach(conv => contactList.appendChild(createContactItem(conv)));
  if (conversations.length === 0) {
    contactList.innerHTML = '<div class="text-center py-4"><p class="text-xs text-zinc-500">No conversations found.</p></div>';
  }
}

function renderMessages(phone) {
  chatMessages.innerHTML = '';
  if (groupedMessages[phone]) {
    groupedMessages[phone].forEach(msg => {
      const isUser = msg.direction === 'sent';
      const msgClass = isUser ? 'user' : 'contact';
      const bubbleClass = msg.isAI ? 'ai-message' : (msg.priority === 'high' ? 'priority-message' : '');
      chatMessages.innerHTML += `
        <div class="chat-message flex items-end gap-2 ${msgClass}" data-message-id="${msg.id}">
          ${isUser ? `
            <button class="rewrite-btn text-zinc-500 hover:text-neon-cyan mb-2" title="Rewrite with AI">
              <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                <path d="M17.414 2.586a2 2 0 00-2.828 0L7 10.172V13h2.828l7.586-7.586a2 2 0 000-2.828z" />
                <path fill-rule="evenodd" d="M2 6a2 2 0 012-2h4a1 1 0 010 2H4v10h10v-4a1 1 0 112 0v4a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" clip-rule="evenodd" />
              </svg>
            </button>
          ` : ''}
          <div class="max-w-xl">
            ${msg.isAI ? `
              <div class="relative">
                <div class="bubble ${bubbleClass} shadow-sm">
                  <div class="absolute -top-2 ${isUser ? '-right-2' : '-left-2'} bg-indigo-400 text-white text-xs rounded-full p-1 leading-none">
                    <svg class="w-4 h-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M13.5 4.06c0-1.336-1.616-2.005-2.56-1.06l-4.048 4.048a.858.858 0 0 0-.253.608v3.688a.858.858 0 0 0 .253.608l4.048 4.048c.944.944 2.56.276 2.56-1.06V4.06ZM5.5 8.06v7.28h2V8.06h-2ZM18 12a4 4 0 0 1-4 4h-1v-2h1a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-1V4h1a4 4 0 0 1 4 4v4Z"></path>
                    </svg>
                  </div>
                  <p>${msg.body}</p>
                </div>
              </div>
            ` : `
              <div class="bubble ${bubbleClass} shadow-sm">
                <p>${msg.body}</p>
              </div>
            `}
            <span class="text-xs text-zinc-400 mt-0.25">${formatDate(msg.timestamp)}</span>
            <button class="delete-message text-red-400 text-xs ml-2" onclick="deleteMessage('${msg.id}')">🗑</button>
          </div>
        </div>
      `;
    });
  } else {
    chatMessages.innerHTML = '<div class="text-center py-4"><p class="text-xs text-zinc-500">No messages yet.</p></div>';
  }
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function loadInbox(phone) {
  currentCounterpart = phone;
  const conv = conversations.find(c => c.counterpart === phone);
  conversationTitle.textContent = conv ? (conv.alias || conv.counterpart) : phone;
  conversationPhone.textContent = conv && conv.is_contact ? conv.counterpart : '';
  conversationCount.textContent = `${conv ? conv.count : 0} Messages`;
  addContactBtn.classList.toggle('hidden', conv && conv.is_contact);
  deleteChatBtn.classList.toggle('hidden', !conv);
  if (conv) deleteChatBtn.onclick = () => deleteChat(conv.counterpart);
  renderMessages(phone);
  renderSuggestions([]);
  conversationHeader.classList.remove('hidden');
  conversation.classList.add('active');
  messageInputContainer.classList.remove('hidden');
  if (window.innerWidth < 1024) {
    document.getElementById('contact-list-container').classList.add('hidden');
  }
  if (conv && conv.unread > 0) {
    fetch(`/mark_read/${phone}`, { method: 'POST' })
      .then(res => {
        if (res.ok) {
          conv.unread = 0;
          renderContacts();
        } else {
          console.error('Failed to mark messages as read');
        }
      })
      .catch(err => console.error('Error marking messages as read:', err));
  }
}

function renderSuggestions(suggestions) {
  suggestionsContainer.innerHTML = suggestions.map(s => `
    <button class="suggestion-btn bg-zinc-800 text-white text-xs px-3 py-1.5 rounded-full hover:bg-neon-cyan hover:text-black transition-colors">
      ${s}
    </button>
  `).join('');
}

function toggleSlider(activeTab) {
  const isActive = aiSlider.classList.contains('active');
  if (isActive) {
    aiSlider.classList.remove('active');
    backdrop.classList.add('hidden');
  } else {
    aiSlider.classList.add('active');
    backdrop.classList.remove('hidden');
    tabs.forEach(tab => {
      const isCurrentTab = tab.dataset.tab === activeTab;
      tab.classList.toggle('border-neon-cyan', isCurrentTab);
      tab.classList.toggle('text-neon-cyan', isCurrentTab);
      tab.classList.toggle('border-transparent', !isCurrentTab);
      tab.classList.toggle('text-gray-400', !isCurrentTab);
    });
    tabContents.forEach(content => {
      content.classList.toggle('hidden', content.id !== `tab-content-${activeTab}`);
    });
  }
}

let sendMessageTimeout = null;
function sendMessage() {
  const message = messageInput.value.trim();
  if (!message || !currentCounterpart) return;

  // Prevent duplicate sends by checking last sent message
  if (lastSentMessage && lastSentMessage.body === message && (Date.now() - lastSentMessage.timestamp) < 1500) {
    return;
  }

  // Disable button briefly to prevent rapid clicks
  sendMessageBtn.disabled = true;
  clearTimeout(sendMessageTimeout);
  sendMessageTimeout = setTimeout(() => {
    sendMessageBtn.disabled = false;
  }, 500);

  const tempId = 'temp-' + Date.now();
  const tempTimestamp = new Date().toISOString();
  lastSentMessage = { body: message, timestamp: Date.now() };

  if (!groupedMessages[currentCounterpart]) groupedMessages[currentCounterpart] = [];
  groupedMessages[currentCounterpart].push({
    direction: 'sent',
    body: message,
    timestamp: tempTimestamp,
    id: tempId
  });
  renderMessages(currentCounterpart);

  let convIndex = conversations.findIndex(c => c.counterpart === currentCounterpart);
  if (convIndex !== -1) {
    conversations[convIndex].count = (conversations[convIndex].count || 0) + 1;
    conversations[convIndex].last_message = message;
    conversations[convIndex].last_timestamp = tempTimestamp;
    conversations[convIndex].active = true;
    const movedConv = conversations.splice(convIndex, 1)[0];
    conversations.unshift(movedConv);
    renderContacts();
    conversationCount.textContent = `${conversations[0].count} Messages`;
  } else {
    conversations.unshift({
      counterpart: currentCounterpart,
      alias: currentCounterpart,
      is_contact: false,
      count: 1,
      last_message: message,
      last_timestamp: tempTimestamp,
      unread: 0,
      active: true,
      avatar: `https://placehold.co/100x100/6366F1/FFFFFF?text=${currentCounterpart.substring(0, 2)}`
    });
    renderContacts();
  }

  messageInput.value = '';
  messageInput.style.height = 'auto';

  fetch('/send_message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ to_number: currentCounterpart, body: message })
  })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        const tempMsg = groupedMessages[currentCounterpart].find(m => m.id === tempId);
        if (tempMsg) tempMsg.id = data.message_id;
        const tempElem = chatMessages.querySelector(`[data-message-id="${tempId}"]`);
        if (tempElem) tempElem.dataset.messageId = data.message_id;
      } else {
        alert('Failed to send message: ' + (data.error || 'Unknown error'));
      }
    })
    .catch(err => {
      alert('Error sending message: ' + err.message);
    });
}

function deleteMessage(messageId) {
  if (confirm('Delete this message?')) {
    fetch(`/delete_message/${messageId}`, { method: 'POST' })
      .then(res => {
        if (res.ok) {
          const elem = chatMessages.querySelector(`[data-message-id="${messageId}"]`);
          if (elem) elem.remove();
          if (groupedMessages[currentCounterpart]) {
            groupedMessages[currentCounterpart] = groupedMessages[currentCounterpart].filter(m => m.id !== messageId);
            const conv = conversations.find(c => c.counterpart === currentCounterpart);
            if (conv) {
              conv.count = (conv.count || 1) - 1;
              conversationCount.textContent = `${conv.count} Messages`;
              const lastMsg = groupedMessages[currentCounterpart].slice(-1)[0];
              conv.last_message = lastMsg ? lastMsg.body : null;
              conv.last_timestamp = lastMsg ? lastMsg.timestamp : null;
              renderContacts();
            }
          }
        } else {
          alert('Error deleting message');
        }
      })
      .catch(err => alert('Error deleting message: ' + err.message));
  }
}

function deleteChat(phone) {
  if (confirm('Delete this chat?')) {
    fetch(`/delete_chat/${phone}`, { method: 'POST' })
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          delete groupedMessages[phone];
          const convIndex = conversations.findIndex(c => c.counterpart === phone);
          if (convIndex !== -1) {
            conversations.splice(convIndex, 1);
            renderContacts();
          }
          if (currentCounterpart === phone) {
            chatMessages.innerHTML = '<div class="text-center py-4"><p class="text-xs text-zinc-500">Select a conversation to view messages.</p></div>';
            conversation.classList.add('hidden');
            conversationHeader.classList.add('hidden');
            messageInputContainer.classList.add('hidden');
            document.getElementById('contact-list-container').classList.remove('hidden');
            currentCounterpart = null;
          }
        } else {
          alert('Error deleting chat: ' + (data.error || 'Unknown error'));
        }
      })
      .catch(err => alert('Error deleting chat: ' + err.message));
  }
}

function filterConversations() {
  const query = searchInput.value.toLowerCase();
  const filtered = conversations.filter(conv => {
    const alias = (conv.alias || conv.counterpart).toLowerCase();
    const phone = conv.counterpart.toLowerCase();
    const lastMessage = (conv.last_message || '').toLowerCase();
    return alias.includes(query) || phone.includes(query) || lastMessage.includes(query);
  });
  contactList.innerHTML = '';
  filtered.forEach(conv => contactList.appendChild(createContactItem(conv)));
  if (filtered.length === 0) {
    contactList.innerHTML = '<div class="text-center py-4"><p class="text-xs text-zinc-500">No conversations found.</p></div>';
  }
}

document.getElementById('addContactForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const formData = new FormData(document.getElementById('addContactForm'));
  const addContactBtn = document.getElementById('addContactBtn');
  addContactBtn.disabled = true;
  addContactBtn.querySelector('.btn-text').classList.add('hidden');
  addContactBtn.querySelector('.btn-loader').classList.remove('hidden');
  try {
    const response = await fetch('/add_contact', {
      method: 'POST',
      body: formData
    });
    const data = await response.json();
    if (data.success) {
      closeModal('addContactModal');
      const phone = formData.get('phone_number');
      const alias = formData.get('alias') || phone;
      const label = formData.get('label') || 'N/A';
      let conv = conversations.find(c => c.counterpart === phone);
      if (conv) {
        conv.alias = alias;
        conv.label = label;
        conv.is_contact = true;
        conv.active = true;
      } else {
        conversations.unshift({
          counterpart: phone,
          alias: alias,
          label: label,
          is_contact: true,
          count: 0,
          last_message: null,
          last_timestamp: null,
          unread: 0,
          active: true,
          avatar: `https://placehold.co/100x100/6366F1/FFFFFF?text=${alias.substring(0, 2)}`
        });
      }
      renderContacts();
      loadInbox(phone);
    } else {
      document.getElementById('contactPhoneError').textContent = data.error || 'Error adding contact';
      document.getElementById('contactPhoneError').classList.remove('hidden');
    }
  } catch (err) {
    document.getElementById('contactPhoneError').textContent = 'Error adding contact: ' + err.message;
    document.getElementById('contactPhoneError').classList.remove('hidden');
  } finally {
    addContactBtn.disabled = false;
    addContactBtn.querySelector('.btn-text').classList.remove('hidden');
    addContactBtn.querySelector('.btn-loader').classList.add('hidden');
  }
});

contactList.addEventListener('click', (e) => {
  const item = e.target.closest('.contact-item');
  if (item) {
    conversations.forEach(c => c.active = c.counterpart === item.dataset.counterpart);
    loadInbox(item.dataset.counterpart);
  }
});

document.getElementById('back-btn').addEventListener('click', () => {
  conversation.classList.remove('active');
  document.getElementById('contact-list-container').classList.remove('hidden');
  conversations.forEach(c => c.active = false);
  currentCounterpart = null;
});

refreshInbox.addEventListener('click', () => {
  const refreshIcon = document.getElementById('refresh-icon');
  refreshIcon.classList.add('rotate');
  setTimeout(() => {
    location.reload();
  }, 500);
});

aiControlsBtn.addEventListener('click', () => toggleSlider('controls'));
aiSummaryBtn.addEventListener('click', () => {
  toggleSlider('summary');
  if (currentCounterpart) {
    summaryText.classList.remove('opacity-100');
    summaryText.classList.add('opacity-0');
    summaryLoader.classList.remove('hidden');
    fetch(`/generate_summary/${currentCounterpart}`)
      .then(res => res.json())
      .then(data => {
        if (data.summary) {
          summaryText.textContent = data.summary;
        } else {
          summaryText.textContent = 'Error generating summary: ' + (data.error || 'Unknown error');
        }
        summaryText.classList.remove('opacity-0');
        summaryText.classList.add('opacity-100');
        summaryLoader.classList.add('hidden');
      })
      .catch(err => {
        summaryText.textContent = 'Error: ' + err.message;
        summaryText.classList.remove('opacity-0');
        summaryText.classList.add('opacity-100');
        summaryLoader.classList.add('hidden');
      });
  } else {
    summaryText.textContent = 'Select a conversation to view its summary.';
    summaryText.classList.remove('opacity-0');
    summaryText.classList.add('opacity-100');
    summaryLoader.classList.add('hidden');
  }
});

closeSliderBtn.addEventListener('click', () => toggleSlider());
backdrop.addEventListener('click', () => toggleSlider());

tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    const tabName = tab.dataset.tab;
    tabs.forEach(t => {
      t.classList.remove('border-neon-cyan', 'text-neon-cyan');
      t.classList.add('border-transparent', 'text-gray-400');
    });
    tab.classList.add('border-neon-cyan', 'text-neon-cyan');
    tab.classList.remove('border-transparent', 'text-gray-400');
    tabContents.forEach(content => {
      content.classList.toggle('hidden', content.id !== `tab-content-${tabName}`);
    });
  });
});

regenerateSuggestionsBtn.addEventListener('click', () => {
  if (!currentCounterpart) return;
  regenerateSuggestionsBtn.querySelector('svg').classList.add('animate-spin');
  fetch(`/generate_suggestions/${currentCounterpart}`)
    .then(res => res.json())
    .then(data => {
      if (data.suggestions) {
        renderSuggestions(data.suggestions);
      } else {
        alert('Error generating suggestions: ' + (data.error || 'Unknown error'));
      }
    })
    .catch(err => alert('Error: ' + err.message))
    .finally(() => regenerateSuggestionsBtn.querySelector('svg').classList.remove('animate-spin'));
});

document.getElementById('rewrite-btn').addEventListener('click', () => {
  const message = messageInput.value.trim();
  if (!message) return;
  const btn = document.getElementById('rewrite-btn');
  btn.querySelector('svg').classList.add('animate-spin');
  fetch('/rewrite_message', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  })
    .then(res => res.json())
    .then(data => {
      if (data.rewritten) {
        messageInput.value = data.rewritten;
        messageInput.style.height = 'auto';
        messageInput.style.height = (Math.min(messageInput.scrollHeight, 120)) + 'px';
      } else {
        alert('Error rewriting message: ' + (data.error || 'Unknown error'));
      }
    })
    .catch(err => alert('Error: ' + err.message))
    .finally(() => btn.querySelector('svg').classList.remove('animate-spin'));
});

suggestionsContainer.addEventListener('click', e => {
  if (e.target.classList.contains('suggestion-btn')) {
    messageInput.value = e.target.textContent.trim();
    messageInput.style.height = 'auto';
    messageInput.style.height = (Math.min(messageInput.scrollHeight, 120)) + 'px';
  }
});

sendMessageBtn.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

socket.on('connect', () => {
  console.log('Socket.IO connected');
  socket.emit('join_room', `user_${selectedPhoneId}`);
  // Start heartbeat to keep connection alive
  setInterval(() => {
    socket.emit('heartbeat', {});
  }, 30000); // Every 30 seconds
});

socket.on('disconnect', () => {
  console.log('Socket.IO disconnected');
});

socket.on('new_message', (data) => {
  const counterpart = data.direction === 'received' ? data.from_number : data.to_number;
  if (!counterpart) return;

  if (!groupedMessages[counterpart]) groupedMessages[counterpart] = [];

  // Check recent 5 messages for duplicates within 3 seconds window
  const recentMessages = groupedMessages[counterpart].slice(-5);
  const newTimestamp = new Date(data.timestamp).getTime();

  for (const msg of recentMessages) {
    const existingTimestamp = new Date(msg.timestamp).getTime();
    if (
      msg.body === data.body &&
      msg.direction === data.direction &&
      Math.abs(newTimestamp - existingTimestamp) < 3000
    ) {
      // Duplicate found, ignore incoming message
      console.log('Duplicate message ignored:', data);
      return;
    }
  }

  // Not a duplicate - add message
  groupedMessages[counterpart].push({
    direction: data.direction,
    body: data.body,
    timestamp: data.timestamp,
    id: data.id,
    priority: data.priority,
    isAI: false
  });

  let convIndex = conversations.findIndex(c => c.counterpart === counterpart);
  if (convIndex === -1) {
    conversations.unshift({
      counterpart,
      alias: counterpart,
      is_contact: false,
      count: 1,
      last_message: data.body,
      last_timestamp: data.timestamp,
      unread: data.direction === 'received' ? 1 : 0,
      active: false,
      avatar: `https://placehold.co/100x100/6366F1/FFFFFF?text=${counterpart.substring(0, 2)}`
    });
  } else {
    conversations[convIndex].count = (conversations[convIndex].count || 0) + 1;
    conversations[convIndex].last_message = data.body;
    conversations[convIndex].last_timestamp = data.timestamp;
    if (data.direction === 'received' && currentCounterpart !== counterpart) {
      conversations[convIndex].unread = (conversations[convIndex].unread || 0) + 1;
    }
    const movedConv = conversations.splice(convIndex, 1)[0];
    conversations.unshift(movedConv);
  }

  renderContacts();
  if (currentCounterpart === counterpart) {
    renderMessages(counterpart);
    conversationCount.textContent = `${conversations[0].count} Messages`;
    if (data.direction === 'received') {
      fetch(`/mark_read/${counterpart}`, { method: 'POST' })
        .then(res => {
          if (res.ok) {
            conversations[0].unread = 0;
            renderContacts();
          }
        });
    }
  }
});

socket.on('update_contact', (data) => {
  const conv = conversations.find(c => c.counterpart === data.phone_number);
  if (conv) {
    conv.alias = data.alias;
    conv.is_contact = true;
    conv.label = data.label || 'N/A';
    renderContacts();
    if (currentCounterpart === data.phone_number) {
      conversationTitle.textContent = data.alias;
      conversationPhone.textContent = data.phone_number;
      conversationCount.textContent = `${conv.count} Messages`;
      addContactBtn.classList.add('hidden');
    }
  } else {
    conversations.unshift({
      counterpart: data.phone_number,
      alias: data.alias,
      is_contact: true,
      label: data.label || 'N/A',
      count: 0,
      last_message: null,
      last_timestamp: null,
      unread: 0,
      active: false,
      avatar: `https://placehold.co/100x100/6366F1/FFFFFF?text=${data.alias.substring(0, 2)}`
    });
    renderContacts();
  }
});

socket.on('update_contact_label', (data) => {
  const conv = conversations.find(c => c.counterpart === data.phone_number);
  if (conv) {
    conv.label = data.label;
    conv.alias = data.alias || conv.alias;
    conv.is_contact = true;
    renderContacts();
    if (currentCounterpart === data.phone_number) {
      conversationTitle.textContent = conv.alias;
      conversationPhone.textContent = data.phone_number;
    }
  }
});

socket.on('contact_deleted', (data) => {
  const conv = conversations.find(c => c.counterpart === data.phone_number);
  if (conv) {
    conv.alias = conv.counterpart;
    conv.is_contact = false;
    conv.label = 'N/A';
    renderContacts();
    if (currentCounterpart === data.phone_number) {
      conversationTitle.textContent = conv.counterpart;
      conversationPhone.textContent = '';
      conversationCount.textContent = `${conv.count} Messages`;
      addContactBtn.classList.remove('hidden');
    }
  }
});

socket.on('message_deleted', (data) => {
  const elem = chatMessages.querySelector(`[data-message-id="${data.message_id}"]`);
  if (elem) elem.remove();
  if (groupedMessages[currentCounterpart]) {
    groupedMessages[currentCounterpart] = groupedMessages[currentCounterpart].filter(m => m.id !== data.message_id);
    const conv = conversations.find(c => c.counterpart === currentCounterpart);
    if (conv) {
      conv.count = (conv.count || 1) - 1;
      conversationCount.textContent = `${conv.count} Messages`;
      const lastMsg = groupedMessages[currentCounterpart].slice(-1)[0];
      conv.last_message = lastMsg ? lastMsg.body : null;
      conv.last_timestamp = lastMsg ? lastMsg.timestamp : null;
      renderContacts();
    }
  }
});

socket.on('chat_deleted', (data) => {
  delete groupedMessages[data.phone_number];
  const convIndex = conversations.findIndex(c => c.counterpart === data.phone_number);
  if (convIndex !== -1) {
    conversations.splice(convIndex, 1);
    renderContacts();
  }
  if (currentCounterpart === data.phone_number) {
    chatMessages.innerHTML = '<div class="text-center py-4"><p class="text-xs text-zinc-500">Select a conversation to view messages.</p></div>';
    conversation.classList.add('hidden');
    conversationHeader.classList.add('hidden');
    messageInputContainer.classList.add('hidden');
    document.getElementById('contact-list-container').classList.remove('hidden');
    currentCounterpart = null;
  }
});
