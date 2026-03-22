
const API_BASE_URL = 'http://localhost:8000';

export const api = {
    async get(endpoint, token) {
        const headers = {
            'Content-Type': 'application/json',
        };
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const response = await fetch(`${API_BASE_URL}${endpoint}`, { headers });
        return response.json();
    },

    async post(endpoint, data, token) {
        const headers = {
            'Content-Type': 'application/json',
        };
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            method: 'POST',
            headers,
            body: JSON.stringify(data),
        });
        return response.json();
    },

    async login(email, password) {
        return this.post('/auth/login', { email, password });
    },

    async createChat(userId, title, token) {
        return this.post('/chat/create', { user_id: userId, title }, token);
    },

    async sendMessage(userId, chatId, messageText, token, persona = 'default', attachments = []) {
        if (attachments && attachments.length > 0) {
            const form = new FormData();
            form.append('user_id', userId);
            if (chatId) form.append('chat_id', chatId);
            form.append('message_text', messageText);
            form.append('persona', persona);
            attachments.forEach((file) => form.append('attachments', file));

            const headers = {};
            if (token) headers['Authorization'] = `Bearer ${token}`;

            const response = await fetch(`${API_BASE_URL}/chat`, {
                method: 'POST',
                headers,
                body: form,
            });
            return response.json();
        }
        return this.post('/chat', { chat_id: chatId || null, message_text: messageText, persona }, token);
    },

    async getMessages(chatId, token) {
        return this.get(`/chat/${chatId}/messages`, token);
    },

    async listChats(token, limit = 10, skip = 0) {
        const qs = `?limit=${encodeURIComponent(limit)}&skip=${encodeURIComponent(skip)}`;
        return this.get(`/chats${qs}`, token);
    }
};
