import React, { useState, useEffect } from 'react';
import { Bars3Icon, ServerIcon } from '@heroicons/react/24/outline';
import { api, getStoredHost, setStoredHost } from '../services/api';

interface HeaderProps {
    activeView: string;
    onMenuClick: () => void;
}

const Header: React.FC<HeaderProps> = ({ activeView, onMenuClick }) => {
    const [currentTime, setCurrentTime] = useState(new Date().toLocaleTimeString());
    const [showHostModal, setShowHostModal] = useState(false);
    const [hostInput, setHostInput] = useState(getStoredHost());

    useEffect(() => {
        const timer = setInterval(() => {
            setCurrentTime(new Date().toLocaleTimeString('en-GB', { hour12: false }));
        }, 1000);
        return () => clearInterval(timer);
    }, []);

    const handlePanic = async () => {
        try {
            await api.triggerPanic();
            alert("WARNING: PANIC PROTOCOL INITIATED!");
        } catch (err) {
            console.error(err);
        }
    };

    const handleSaveHost = () => {
        if (!hostInput.trim()) return;
        setStoredHost(hostInput);
        setShowHostModal(false);
        window.location.reload();
    };

    return (
        <>
            <header className="h-16 border-b border-cyan/20 bg-[#050510]/80 backdrop-blur flex items-center justify-between px-3 md:px-6">
                <div className="flex items-center space-x-2 md:space-x-4">
                    <button
                        aria-label="Open Menu"
                        className="text-cyan p-1.5 hover:bg-cyan/10 rounded"
                        onClick={onMenuClick}
                    >
                        <Bars3Icon className="w-7 h-7 md:w-8 md:h-8" />
                    </button>
                    <div className="text-sm md:text-xl font-orbitron text-white tracking-widest uppercase truncate max-w-[120px] xs:max-w-none">
                        {activeView.replace('_', ' ')} <span className="text-cyan hidden xs:inline">///VIEW</span>
                    </div>
                </div>

                <div className="flex items-center space-x-2 md:space-x-4">
                    <button
                        className="btn-danger text-xs px-2 py-1 md:px-4 md:py-2 animate-pulse"
                        onClick={handlePanic}
                    >
                        PANIC
                    </button>

                    <button
                        title="Server Connection Settings"
                        onClick={() => setShowHostModal(true)}
                        className="p-1.5 border border-cyan/30 rounded text-cyan hover:bg-cyan/10 transition-colors flex items-center gap-1 text-xs font-mono"
                    >
                        <ServerIcon className="w-5 h-5 text-cyan" />
                        <span className="hidden lg:inline">{getStoredHost()}</span>
                    </button>

                    <div className="text-right hidden md:block">
                        <div className="text-[10px] text-gray-400">SYSTEM TIME</div>
                        <div className="text-lg font-mono text-white leading-none">{currentTime}</div>
                    </div>
                    <div className="h-8 w-[1px] bg-cyan/20 hidden md:block" />
                    <div className="flex items-center space-x-2">
                        <span className="h-2 w-2 bg-green-500 rounded-full animate-pulse" />
                        <span className="text-xs md:text-sm font-bold text-green-500 tracking-wider">ONLINE</span>
                    </div>
                </div>
            </header>

            {showHostModal && (
                <div className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
                    <div className="bg-[#0a0a18] border border-cyan/40 p-6 rounded-lg max-w-md w-full shadow-[0_0_30px_rgba(0,240,255,0.2)]">
                        <h3 className="text-lg font-orbitron text-cyan font-bold mb-2">SERVER CONNECTION</h3>
                        <p className="text-xs text-gray-400 mb-4">
                            Enter the IP address and port of your Guardian server (e.g. 192.168.1.100:8000).
                        </p>
                        <input
                            type="text"
                            value={hostInput}
                            onChange={(e) => setHostInput(e.target.value)}
                            placeholder="192.168.1.100:8000"
                            className="w-full text-sm font-mono bg-black/60 border border-cyan/40 rounded p-2 text-white mb-4 focus:outline-none focus:border-cyan"
                        />
                        <div className="flex justify-end gap-3">
                            <button
                                onClick={() => setShowHostModal(false)}
                                className="px-4 py-2 border border-gray-600 rounded text-xs text-gray-300 hover:bg-white/5"
                            >
                                CANCEL
                            </button>
                            <button
                                onClick={handleSaveHost}
                                className="btn-primary text-xs"
                            >
                                SAVE & RECONNECT
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};

export default Header;
