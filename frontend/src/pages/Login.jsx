import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Mail, Lock, ArrowRight, Github, Chrome, Eye, EyeOff, AlertCircle } from 'lucide-react';

const Login = () => {
    const navigate = useNavigate();

    const [isLogin, setIsLogin] = useState(true);
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(false);
    const [showPassword, setShowPassword] = useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError(null);
        setLoading(true);

        const endpoint = isLogin ? '/auth/login' : '/auth/register';

        try {
            const res = await fetch(`http://localhost:8000${endpoint}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });

            const data = await res.json();

            if (res.ok && data.status === 'ok') {
                localStorage.setItem('auth_token', data.access_token);
                localStorage.setItem('user_id', data.user_id);
                localStorage.setItem('user_email', email);
                navigate('/chat');
            } else {
                setError(data.detail || `${isLogin ? 'Login' : 'Registration'} failed.`);
            }
        } catch {
            setError('Network error. Is the backend running?');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="relative min-h-screen w-full flex items-center justify-center overflow-hidden font-sans selection:bg-white/20">

            <div className="lux-bg-glow"></div>

            <div className="relative z-10 w-full max-w-[440px] lux-card p-10 reveal-up">

                {/* Header */}
                <div className="flex flex-col items-center gap-5 mb-8">

                    <div className="w-10 h-10 rounded-md border border-black/70 flex items-center justify-center bg-black">
                        <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" fill="none"
                            stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <rect width="16" height="12" x="4" y="8" rx="2" />
                            <path d="M12 8V4H8" />
                            <path d="M2 14h2M20 14h2M9 13v2M15 13v2" />
                        </svg>
                    </div>

                    <div className="text-center">
                        <h1 className="text-[26px] font-bold text-black tracking-tight">
                            {isLogin ? 'Welcome Back' : 'Create Account'}
                        </h1>

                        <p className="text-text-muted text-[13px] mt-1">
                            {isLogin
                                ? "Sign in to Shashank's AI to continue"
                                : "Create your Shashank's AI account"}
                        </p>
                    </div>
                </div>

                {/* Form */}
                <form onSubmit={handleSubmit} className="space-y-5">

                    {error && (
                        <div className="flex items-center gap-2 p-3 bg-red-500/15 border border-red-500/30 rounded-xl text-red-400 text-[12px]">
                            <AlertCircle size={16} />
                            {error}
                        </div>
                    )}

                    {/* Email */}
                    <div>
                        <label className="text-[11px] text-text-muted mb-1 block">
                            Email
                        </label>

                        <div className="relative group">
                            <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />

                            <input
  type="email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                required
                                placeholder="Enter your email"
                                className="w-full bg-white/5 border border-black/10 rounded-xl py-3 pl-11 pr-4 text-[14px] text-red placeholder:text-gray-400 focus:outline-none focus:border-white/30 transition-all"
                            />
                        </div>
                    </div>

                    {/* Password */}
                    <div>
                        <label className="text-[11px] text-text-muted mb-1 block">
                            Password
                        </label>

                        <div className="relative">
                            <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />

                            <input
                                type={showPassword ? 'text' : 'password'}
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                required
                                minLength={6}
                                placeholder="Enter your password"
                                className="w-full bg-white/5 border border-black/10 rounded-xl py-3 pl-11 pr-11 text-[14px] text-red placeholder:text-gray-400 focus:outline-none focus:border-white/30 transition-all"
                            />

                            <button
                                type="button"
                                onClick={() => setShowPassword(!showPassword)}
                                className="absolute right-4 top-1/2 -translate-y-1/2 text-text-muted hover:text-white"
                            >
                                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                            </button>
                        </div>
                    </div>

                    {/* Submit */}
                    <button
                        type="submit"
                        disabled={loading}
                        className="w-full lux-btn-primary py-3 rounded-xl flex items-center justify-center gap-2 mt-2 shadow-lg disabled:opacity-60"
                    >
                        {loading ? (
                            <div className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                        ) : (
                            <>
                                <span className="text-[13px] font-semibold">
                                    {isLogin ? 'Sign In' : 'Sign Up'}
                                </span>
                                <ArrowRight size={16} />
                            </>
                        )}
                    </button>
                </form>

                {/* Toggle */}
                <div className="text-center mt-6">
                    <button
                        type="button"
                        onClick={() => {
                            setIsLogin(!isLogin);
                            setError(null);
                        }}
                        className="text-[13px] text-text-muted hover:text-white"
                    >
                        {isLogin ? "Don't have an account?" : "Already have an account?"}
                        <span className="ml-1 text-white font-medium underline decoration-white/30">
                            {isLogin ? 'Sign up' : 'Sign in'}
                        </span>
                    </button>
                </div>

                {/* Divider */}
                <div className="flex items-center gap-3 my-6">
                    <div className="flex-1 h-[1px] bg-white/10" />
                    <span className="text-[10px] uppercase text-text-muted tracking-widest">or</span>
                    <div className="flex-1 h-[1px] bg-white/10" />
                </div>

                {/* OAuth */}
                <div className="space-y-3">

                    <button className="w-full lux-btn flex items-center justify-center gap-3 py-3 rounded-xl">
                        <Chrome size={18} />
                        Continue with Google
                    </button>

                    <button className="w-full lux-btn flex items-center justify-center gap-3 py-3 rounded-xl">
                        <Github size={18} />
                        Continue with GitHub
                    </button>

                </div>

                {/* Footer */}
                <p className="mt-8 text-center text-[11px] text-text-muted max-w-[280px] mx-auto">
                    By continuing you agree to the
                    <span className="mx-1 underline hover:text-white cursor-pointer">
                        Terms
                    </span>
                    and
                    <span className="ml-1 underline hover:text-white cursor-pointer">
                        Privacy Policy
                    </span>.
                </p>

            </div>
        </div>
    );
};

export default Login;