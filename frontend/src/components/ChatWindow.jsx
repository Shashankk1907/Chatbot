
import React, { useRef, useEffect } from 'react';
import {
    ChevronDown,
    Share2,
    HelpCircle,
    MessageSquare,
    User,
    Bot,
    Copy,
    Paperclip
} from 'lucide-react';
import InputBar from './InputBar'; // We bring InputBar inside for the centered layout


import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const Message = ({ id, role, content, attachments = [] }) => {
    const handleCopy = () => {
        if (navigator.clipboard) {
            navigator.clipboard.writeText(content);
        }
    };

    return (
        <div
            id={id ? `msg-${id}` : undefined}
            className={`group relative flex gap-5 w-full py-6 px-10 fade-in-up transition-colors duration-500 ${role === 'assistant' ? 'bg-[rgba(0,0,0,0.01)] border-y border-[rgba(0,0,0,0.03)]' : ''}`}
        >
            <div className={`w-9 h-9 flex items-center justify-center shrink-0 rounded-xl ${role === 'assistant'
                ? 'bg-black text-white shrink-0'
                : 'bg-[rgba(0,0,0,0.05)] text-text-main'
                }`}>
                {role === 'assistant' ? <Bot className="w-5 h-5" /> : <User className="w-5 h-5" />}
            </div>
            <div className="flex-1 space-y-1.5 pt-0.5 overflow-hidden">
                <p className="text-[12px] font-semibold text-text-main">{role === 'assistant' ? "Shashank's AI" : 'You'}</p>

                {/* attachments list at top */}
                {attachments && attachments.length > 0 && (
                    <div className="flex flex-wrap gap-2 mb-3">
                        {attachments.map((a, i) => {
                            // Ensure full URL for backend uploads if relative
                            const url = a.url || '';
                            const fullUrl = url.startsWith('http') || url.startsWith('blob:')
                                ? url
                                : `http://localhost:8000${url.startsWith('/') ? '' : '/'}${url}`;
                            return (
                                <a
                                    key={i}
                                    href={fullUrl}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="flex items-center gap-2 bg-[rgba(0,0,0,0.03)] px-2.5 py-1.5 rounded-lg text-[12px] text-text-dim border border-black/5 hover:bg-[rgba(0,0,0,0.06)] hover:text-text-main transition-all duration-200"
                                >
                                    <Paperclip className="w-3.5 h-3.5 opacity-60" />
                                    <span className="truncate max-w-[200px]">{a.filename}</span>
                                </a>
                            );
                        })}
                    </div>
                )}

                <div className="text-[15px] leading-[1.7] text-text-dim font-light markdown-content">
                    {role === 'assistant' ? (
                        <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                                h1: ({ node, ...props }) => <h1 className="text-xl font-bold mt-4 mb-2 text-text-main" {...props} />,
                                h2: ({ node, ...props }) => <h2 className="text-lg font-bold mt-4 mb-2 text-text-main" {...props} />,
                                h3: ({ node, ...props }) => <h3 className="text-md font-bold mt-3 mb-1 text-text-main" {...props} />,
                                p: ({ node, ...props }) => <p className="mb-4 last:mb-0" {...props} />,
                                ul: ({ node, ...props }) => <ul className="list-disc ml-5 mb-4 space-y-1" {...props} />,
                                ol: ({ node, ...props }) => <ol className="list-decimal ml-5 mb-4 space-y-1" {...props} />,
                                li: ({ node, ...props }) => <li className="pl-1" {...props} />,
                                blockquote: ({ node, ...props }) => (
                                    <blockquote className="border-l-4 border-black/10 pl-4 py-1 italic bg-black/5 rounded-r-lg my-4" {...props} />
                                ),
                                code: ({ node, inline, ...props }) => (
                                    inline
                                        ? <code className="bg-black/5 px-1.5 py-0.5 rounded text-sm text-text-main font-mono" {...props} />
                                        : <code className="block bg-black/5 p-4 rounded-xl text-sm text-text-dim font-mono my-4 overflow-x-auto border border-black/5" {...props} />
                                ),
                                strong: ({ node, ...props }) => <strong className="font-bold text-text-main" {...props} />
                            }}
                        >
                            {content}
                        </ReactMarkdown>
                    ) : (
                        content
                    )}

                    <button
                        onClick={handleCopy}
                        className="absolute bottom-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-text-muted hover:text-white"
                        title="Copy message"
                    >
                        <Copy className="w-4 h-4" />
                    </button>
                </div>
            </div>
        </div>
    );
};

const ChatWindow = ({ messages = [], loading = false, onSendMessage, persona = 'default', setPersona = () => { }, onVisibleMessageChange = () => { } }) => {
    const scrollRef = useRef(null);
    const lastReported = useRef(null);

    // compute which user message is at top of viewport
    const handleScroll = () => {
        if (!scrollRef.current) return;
        const containerRect = scrollRef.current.getBoundingClientRect();
        let closestId = null;
        let closestOffset = Infinity;
        messages.forEach(m => {
            if (m.role !== 'user') return;
            const el = document.getElementById(`msg-${m.id}`);
            if (!el) return;
            const r = el.getBoundingClientRect();
            const offset = Math.abs(r.top - containerRect.top);
            if (offset < closestOffset) {
                closestOffset = offset;
                closestId = m.id || m.originalIdx;
            }
        });
        if (closestId && closestId !== lastReported.current) {
            lastReported.current = closestId;
            onVisibleMessageChange(closestId);
        }
    };

    useEffect(() => {
        if (scrollRef.current && messages.length > 0) {
            scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
        }
    }, [messages, loading]);

    return (
        <main className="flex-1 h-full w-full relative flex flex-col overflow-hidden">
            {/* ── Top Navigation Bar ── */}
            <header className="px-8 py-6 flex justify-between items-center w-full z-10 fade-in-up">
                {/* we removed persona label from header; keep plan info */}
                <div className="flex flex-col">
                    <h2 className="text-[16px] font-semibold text-text-main">Conversation</h2>
                    <span className="text-[11px] text-text-muted">Free Plan</span>
                </div>

                <div className="flex items-center gap-3">
                    <button className="lux-btn px-4 py-2 rounded-xl flex items-center gap-2 text-[12px] font-medium text-text-dim hover:text-text-main">
                        <Share2 className="w-4 h-4" /> Share
                    </button>
                    <button className="lux-btn px-4 py-2 rounded-xl flex items-center gap-2 text-[12px] font-medium text-text-dim hover:text-text-main">
                        <HelpCircle className="w-4 h-4" /> Help
                    </button>
                </div>
            </header>

            {/* ── Main Scrollable Area ── */}
            <div
                ref={scrollRef}
                className="flex-1 overflow-y-auto w-full flex flex-col pb-[96px] scroll-smooth"
            >
                {messages.length === 0 ? (
                    <div className="flex-1 flex flex-col items-center justify-center w-full max-w-[840px] px-8 mx-auto -mt-10">
                        

                        {/* ── Centered Input Block ── */}
                        <div className="w-full mb-10">
                            <InputBar onSend={onSendMessage} disabled={loading} persona={persona} setPersona={setPersona} />
                        </div>

                        {/* ── Recent Chats Section ── */}
                        <div className="w-full max-w-[800px] mt-auto">
                            <button className="flex items-center gap-2 text-[12px] font-semibold text-text-main hover:text-black mb-4 transition-colors">
                                <MessageSquare className="w-4 h-4" /> Your Recent chats <ChevronDown className="w-3.5 h-3.5" />
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="w-full flex flex-col flex-1 items-start px-8">
                        <div className="w-full max-w-4xl flex-1">
                            {messages.map((m, i) => (
                                <Message key={m.id || i} {...m} />
                            ))}
                            {loading && (
                                <div className="flex gap-5 w-full py-6 px-10 fade-in-up bg-[rgba(0,0,0,0.01)] border-y border-[rgba(0,0,0,0.03)]">
                                    <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0 bg-black text-white">
                                        <Bot className="w-5 h-5" />
                                    </div>
                                    <div className="flex-1 pt-3 flex gap-1.5">
                                        <div className="w-2 h-2 rounded-full bg-black/20 animate-pulse transition-all"></div>
                                        <div className="w-2 h-2 rounded-full bg-black/20 animate-pulse delay-100 transition-all"></div>
                                        <div className="w-2 h-2 rounded-full bg-black/20 animate-pulse delay-200 transition-all"></div>
                                    </div>
                                </div>
                            )}
                        </div>

                        {/* Stick Input bar to bottom when in thread view */}
                        <div className="sticky bottom-0 left-0 right-0 pt-6 pb-8 px-8 bg-gradient-to-t from-[var(--bg-gradient-end)] via-[#ffffffcc] to-transparent w-full">
                            <div className="max-w-4xl mx-auto">
                                <InputBar onSend={onSendMessage} disabled={loading} persona={persona} setPersona={setPersona} />
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </main>
    );
};

export default ChatWindow;
