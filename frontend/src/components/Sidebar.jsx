
import React, { useState } from 'react';
import {
    Plus,
    MessageSquare,
    Search,
    ChevronDown,
    Settings
} from 'lucide-react';

const NavItem = ({ icon: Icon, label, active = false, onClick = () => { } }) => (
    <div
        onClick={onClick}
        className={`flex items-center gap-3 px-3 py-2.5 mx-2 rounded-xl cursor-pointer transition-all duration-200 group
            ${active
                ? 'bg-[rgba(0,0,0,0.04)] text-text-main'
                : 'text-text-dim hover:text-text-main hover:bg-[rgba(0,0,0,0.02)]'
            }`}
    >
        <Icon className={`w-4 h-4 shrink-0 opacity-80 ${active ? 'opacity-100' : 'group-hover:opacity-100'}`} />
        <span className="text-[13px] font-medium tracking-tight truncate flex-1">{label}</span>
        {active && (
            <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-text-main shadow-[0_0_8px_rgba(0,0,0,0.1)]" />
        )}
    </div>
);

const UserSettings = ({ email = '', onLogout = () => { } }) => (
    <div className="mt-4 px-2">
        <div className="flex items-center justify-between px-3 py-2 rounded-xl hover:bg-[rgba(0,0,0,0.03)] transition-colors group border border-transparent">
            <div className="flex items-center gap-2 overflow-hidden">
                <div className="w-6 h-6 rounded-full bg-black flex items-center justify-center shrink-0">
                    <span className="text-[10px] font-bold text-white pt-0.5">{(email || 'G').slice(0, 2).toUpperCase()}</span>
                </div>
                <span className="text-[12px] font-medium text-text-muted group-hover:text-text-main truncate transition-colors">{email || 'Guest'}</span>
            </div>
            <div className="flex items-center gap-2">
                <button onClick={onLogout} className="text-[11px] text-text-muted hover:text-text-main px-3 py-1 rounded-md border border-transparent hover:border-black/5">Logout</button>
                <ChevronDown className="w-3.5 h-3.5 text-text-muted group-hover:text-text-main shrink-0" />
            </div>
        </div>
    </div>
);

const Sidebar = ({ chats = [], activeChatId = null, onSelectChat = () => { }, onNewChat = () => { }, onShowMore = () => { }, userEmail = '', onLogout = () => { } }) => {
    return (
        <aside className="w-[280px] h-full flex flex-col pt-6 pb-4 shrink-0 z-20 backdrop-blur-md bg-[var(--sidebar-bg)]/80 border-r border-[var(--border-subtle)]">
            {/* ── Brand + search ── */}
            <div className="px-5 mb-6">
                <div className="flex items-center gap-3 mb-4">
                    <div className="w-8 h-8 rounded-[4px] border border-black/10 flex items-center justify-center bg-black">
                        {/* Bot SVG - white and slightly larger */}
                        <svg
                            xmlns="http://www.w3.org/2000/svg"
                            width="24"
                            height="24"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="white"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            className="w-13 h-13 text-white"
                            aria-hidden="true"
                        >
                            <path d="M12 8V4H8"></path>
                            <rect width="16" height="12" x="4" y="8" rx="2"></rect>
                            <path d="M2 14h2"></path>
                            <path d="M20 14h2"></path>
                            <path d="M15 13v2"></path>
                            <path d="M9 13v2"></path>
                        </svg>
                    </div>
                </div>
                <div className="relative">
                    <input
                        type="text"
                        placeholder="Search chats..."
                        className="w-full lux-input pl-10 pr-4 py-2 rounded-xl text-[13px] bg-[rgba(0,0,0,0.02)] border border-[rgba(0,0,0,0.05)] focus:bg-[rgba(0,0,0,0.03)] transition-colors"
                    />
                    <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-text-muted" />
                </div>
            </div>

            {/* ── New Chat CTA ── */}
            <div className="px-5 mb-6">
                <button onClick={onNewChat} className="flex items-center justify-between w-full px-4 py-2.5 rounded-[14px] bg-[rgba(0,0,0,0.02)] border border-[rgba(0,0,0,0.05)] hover:bg-[rgba(0,0,0,0.04)] hover:border-[rgba(0,0,0,0.1)] transition-all duration-300 group">
                    <div className="flex items-center gap-2.5">
                        <Plus className="w-4 h-4 text-text-main group-hover:scale-110 transition-transform" />
                        <span className="text-[13px] font-medium text-text-main">New Chat</span>
                    </div>
                </button>
            </div>

            {/* ── Recent / Chats ── */}
            <div className="flex-1 overflow-y-auto px-1 space-y-0.5 scrollbar-hide">
                <div className="px-4 mb-2 mt-2">
                    <p className="text-[10px] font-semibold text-text-muted">Recent</p>
                </div>
                {chats.length === 0 ? (
                    <div className="px-4 text-[12px] text-text-muted">No chats yet. Start a new chat to see it here.</div>
                ) : (
                    chats.map((c) => (
                        <NavItem
                            key={c.chat_id}
                            icon={MessageSquare}
                            label={c.title || 'Untitled'}
                            active={c.chat_id === activeChatId}
                            onClick={() => onSelectChat(c.chat_id)}
                        />
                    ))
                )}

                <div onClick={onShowMore} className="flex items-center gap-3 px-3 py-2.5 mx-2 rounded-xl cursor-pointer text-text-muted hover:text-text-main hover:bg-[rgba(0,0,0,0.02)] transition-colors mt-1 group">
                    <ChevronDown className="w-4 h-4 shrink-0 opacity-80 group-hover:opacity-100" />
                    <span className="text-[12px] font-medium tracking-tight">Show more</span>
                </div>
            </div>

            {/* ── Bottom Section ── */}
            <div className="mt-auto px-1 pt-4">

                <NavItem icon={Settings} label="Settings" />



                <UserSettings email={userEmail} onLogout={onLogout} />
            </div>
        </aside>
    );
};

export default Sidebar;
