import React, { useState } from 'react';
import { Compass } from 'lucide-react';

const MessageNavigator = ({ messages = [], onNavigate, activeId = null }) => {
    const [open, setOpen] = useState(false);

    const userMessages = messages
        .map((msg, originalIdx) => ({ ...msg, originalIdx }))
        .filter(msg => msg.role === 'user');

    if (userMessages.length === 0) return null;

    return (
        <>
            {/* ── Visual Handle / Trigger Zone ── */}
            <div
                className="fixed top-1/2 -translate-y-1/2 right-0 z-[60]"
                onMouseEnter={() => setOpen(true)}
            >
                <button
                    className="flex flex-col items-center justify-center w-[18px] h-40 bg-[rgba(0,0,0,0.08)] hover:bg-[rgba(19,19,19,0.15)] border-y border-l border-[rgba(69,69,69,0.1)] rounded-l-full backdrop-md cursor-pointer group shadow-2xl"
                >
                    <div className="w-[5px] h-12 bg-black rounded-full" />
                </button>
            </div>

            {/* ── Navigator Sidebar ── */}
            <aside
                onMouseLeave={() => setOpen(false)}
                className={`fixed top-0 right-0 w-72 h-full z-[70] border-l border-[var(--border-subtle)] 
                bg-[var(--sidebar-bg)] flex flex-col shadow-2xl backdrop-blur-md bg-opacity-40
                transform transition-transform duration-300 ease-out
                ${open ? 'translate-x-0' : 'translate-x-full'}`}
            >
                <div className="p-6 border-b border-[var(--border-subtle)]">
                    <h3 className="text-[13px] font-semibold text-black flex items-center gap-2">
                        <Compass className="w-4 h-4 text-text-muted" />
                        Navigator
                    </h3>
                </div>

                <div className="flex-1 overflow-y-auto py-4 px-3 space-y-1 custom-scrollbar">
                    {userMessages.map((msg) => (
                        <button
                            key={msg.id || msg.originalIdx}
                            onClick={() => {
                                onNavigate(msg.id || msg.originalIdx);
                                setOpen(false);
                            }}
                            className={`w-full flex flex-col gap-1.5 p-4 rounded-xl transition-all group text-left border ${
                                activeId === (msg.id || msg.originalIdx)
                                    ? 'bg-white/20 text-white border-white/20'
                                    : 'bg-[rgba(255,255,255,0.02)] text-text-dim border-transparent'
                            } hover:bg-[rgba(255,255,255,0.04)] hover:border-[rgba(255,255,255,0.05)]`}
                        >
                            <div className="flex items-center justify-between">
                                <span className="text-[10px] text-text-muted group-hover:text-black transition-colors font-medium">
                                    Message #{msg.originalIdx + 1}
                                </span>
                            </div>

                            <p className="text-[10px] text-text-dim group-hover:text-black line-clamp-2 font-light leading-relaxed">
                                {msg.content || '...'}
                            </p>
                        </button>
                    ))}
                </div>

                <div className="p-4 border-t border-[var(--border-subtle)] bg-[rgba(255,255,255,0.01)]">
                    <p className="text-[10px] text-text-muted text-center italic">
                        Click to jump
                    </p>
                </div>
            </aside>
        </>
    );
};

export default MessageNavigator;