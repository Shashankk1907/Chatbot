import React, { useState, useEffect } from 'react';
import Sidebar from '../components/Sidebar';
import ChatWindow from '../components/ChatWindow';
import MessageNavigator from '../components/MessageNavigator';
import { api } from '../services/api';
import { useNavigate } from 'react-router-dom';

const Chat = () => {
    const [messages, setMessages] = useState([]);
    const [activeMsgId, setActiveMsgId] = useState(null);
    const [chatId, setChatId] = useState(null);
    const [chats, setChats] = useState([]);
    const [userId, setUserId] = useState(localStorage.getItem('user_id') || "guest_user");
    const token = localStorage.getItem('auth_token');
    const [loading, setLoading] = useState(false);
    const [persona, setPersona] = useState('default');

    // Navigation logic: Scroll to a specific message ID
    const handleScrollToMessage = (id) => {
        const element = document.getElementById(`msg-${id}`);
        if (element) {
            element.scrollIntoView({ behavior: 'smooth', block: 'center' });
            // Add a temporary highlight effect
            element.classList.add('bg-white/5');
            setTimeout(() => element.classList.remove('bg-white/5'), 2000);
        }
    };

    // Load user's chats
    useEffect(() => {
        const loadChats = async () => {
            if (!token) return;
            try {
                const res = await api.listChats(token, 10, 0);
                if (res.status === 'ok') {
                    setChats(res.chats || []);
                    // If no chat selected, pick the first
                    if (!chatId && res.chats && res.chats.length > 0) {
                        setChatId(res.chats[0].chat_id);
                    }
                }
            } catch (err) {
                console.error('Failed to load chats', err);
            }
        };
        loadChats();
    }, [token]);

    const loadMoreChats = async () => {
        if (!token) return;
        try {
            const skip = chats.length || 0;
            const res = await api.listChats(token, 10, skip);
            if (res.status === 'ok') {
                const more = res.chats || [];
                if (more.length > 0) setChats(prev => [...prev, ...more]);
            }
        } catch (e) {
            console.error('Failed to load more chats', e);
        }
    };

    // Load messages when chatId or token changes
    useEffect(() => {
        const loadMessages = async () => {
            if (!chatId) return;

            // Clear messages immediately on chat switch to prevent leakage
            setMessages([]);

            // Temp chats have no history yet
            if (String(chatId).startsWith('temp-')) return;

            // Don't attempt without a token — will get 401 silently
            if (!token) {
                console.warn('[Chat] No auth token, skipping message load');
                return;
            }

            try {
                const res = await api.getMessages(chatId, token);
                if (res.status === 'ok') {
                    const msgs = (res.messages || []).map(m => ({
                        id: m.id,
                        role: m.role,
                        content: m.content,
                        createdAt: m.created_at,
                        attachments: m.attachments || [],
                    }));
                    setMessages(msgs);
                } else {
                    console.error('[Chat] getMessages failed:', res.status, res.reason || res);
                }
            } catch (err) {
                console.error('[Chat] Failed to load messages', err);
            }
        };
        loadMessages();
    }, [chatId, token]);

    const navigate = useNavigate();

    const handleLogout = () => {
        // Clear auth state and redirect to login
        localStorage.removeItem('auth_token');
        localStorage.removeItem('user_id');
        localStorage.removeItem('user_email');
        navigate('/login');
    };

    const createNewChat = async () => {
        if (!userId || userId === 'guest_user') return;

        // Optimistic UI only: create a temporary chat entry locally and select it.
        const tempId = `temp-${Date.now()}`;
        const tempChat = {
            chat_id: tempId,
            title: 'New Chat',
            message_count: 0,
            last_active: new Date().toISOString(),
            optimistic: true,
        };
        setChats(prev => [tempChat, ...prev]);
        setChatId(tempId);
        setMessages([]);
    };

    // SSE connection for realtime updates
    // Note: EventSource cannot send Authorization headers — token goes as ?token= query param
    useEffect(() => {
        if (!token || !chatId || chatId.startsWith('temp-')) return;

        // Single SSE connection covers both chat events and user-level events on the server
        const url = `http://localhost:8000/events?chat_id=${chatId}&token=${encodeURIComponent(token)}`;
        let source;
        try {
            source = new EventSource(url);
        } catch (e) {
            return;
        }

        source.onmessage = (e) => {
            try {
                const payload = JSON.parse(e.data);
                if (payload.type === 'message') {
                    const msg = payload.message;
                    setMessages(prev => {
                        if (prev.some(m => m.id === msg.id)) return prev;
                        if (msg.role === 'user' && prev.length > 0) {
                            const last = prev[prev.length - 1];
                            if (last.role === 'user' && last.content === msg.content && !last.id) {
                                return [...prev.slice(0, -1), {
                                    id: msg.id, role: msg.role, content: msg.content,
                                    attachments: msg.attachments || [], createdAt: last.createdAt,
                                }];
                            }
                        }
                        return [...prev, {
                            id: msg.id, role: msg.role, content: msg.content,
                            attachments: msg.attachments || [], createdAt: new Date().toISOString(),
                        }];
                    });
                } else if (payload.type === 'title') {
                    setChats(prev => prev.map(c => c.chat_id === chatId ? { ...c, title: payload.title } : c));
                } else if (payload.type === 'chat_list') {
                    setChats(payload.chats || []);
                }
            } catch (err) { /* ignore parse errors */ }
        };

        source.onerror = () => {
            // Browser will auto-retry; no action needed
        };

        return () => {
            try { source.close(); } catch (e) { }
        };
    }, [chatId, token]);

    const handleSendMessage = async (text, attachments = [], selectedPersona = persona) => {
        if (!text.trim() && (!attachments || attachments.length === 0)) return;

        let currentChatId = chatId;
        setLoading(true);

        try {
            // 1. If no chat selected or temporary, set to null for backend auto-creation
            let isNewChat = false;
            if (!currentChatId || String(currentChatId).startsWith('temp-')) {
                currentChatId = null;
                isNewChat = true;
            }

            // 2. Optimistically add user message (no ID yet)
            // convert File objects to URLs for optimistic UI
            const attachmentInfo = attachments.map(f => {
                try {
                    return { filename: f.name, url: URL.createObjectURL(f) };
                } catch (e) {
                    return { filename: f.name, url: '#' };
                }
            });
            const userMsg = { role: 'user', content: text, attachments: attachmentInfo, createdAt: new Date().toISOString() };
            setMessages(prev => [...prev, userMsg]);

            // 3. Send message to backend
            const res = await api.sendMessage(userId, currentChatId, text, token, selectedPersona, attachments);
            if (res.status === 'ok') {
                if (isNewChat && res.chat_id) {
                    currentChatId = res.chat_id;
                    setChatId(currentChatId);
                }

                const assistantMsg = {
                    id: res.message_id,
                    role: 'assistant',
                    content: res.content,
                    createdAt: new Date().toISOString()
                };

                setMessages(prev => {
                    // Deduplicate in case SSE arrived first
                    if (prev.some(m => m.id === assistantMsg.id)) return prev;
                    return [...prev, assistantMsg];
                });

                // Update the user message ID if we received it (optional but good)
                if (res.user_message_id) {
                    setMessages(prev => prev.map(m => (m.role === 'user' && m.content === text && !m.id) ? { ...m, id: res.user_message_id } : m));
                }

                // refresh chat list metadata
                try {
                    const updated = await api.listChats(token, 10, 0);
                    if (updated.status === 'ok') setChats(updated.chats || []);
                } catch (e) { }
            } else {
                if (isNewChat) {
                    setChats(prev => prev.filter(c => !String(c.chat_id).startsWith('temp-')));
                    setChatId(null);
                }
            }
        } catch (err) {
            console.error("Failed to send message", err);
            if (isNewChat) {
                setChats(prev => prev.filter(c => !String(c.chat_id).startsWith('temp-')));
                setChatId(null);
            }
        } finally {
            setLoading(false);
        }
    };

    const userEmail = localStorage.getItem('user_email') || '';

    return (
        <div className="relative flex h-screen w-screen overflow-hidden text-text-main font-sans bg-bg-color">
            {/* Background Glow Layer */}
            <div className="lux-bg-glow"></div>

            {/* Main Container - Full Screen layout without borders as per new spec */}
            <div className="relative z-10 flex h-full w-full overflow-hidden">
                <Sidebar chats={chats} activeChatId={chatId} onSelectChat={(id) => setChatId(id)} onNewChat={createNewChat} onShowMore={loadMoreChats} userEmail={userEmail} onLogout={handleLogout} />
                <div className="flex-1 flex flex-col items-center relative gap-0 overflow-hidden bg-transparent">

                    <ChatWindow
                        messages={messages}
                        loading={loading}
                        onSendMessage={handleSendMessage}
                        persona={persona}
                        setPersona={setPersona}
                        onVisibleMessageChange={setActiveMsgId}
                    />
                </div>
                <MessageNavigator
                    messages={messages}
                    onNavigate={handleScrollToMessage}
                    activeId={activeMsgId}
                />
            </div>
        </div>
    );
};

export default Chat;
