import React, { useState, useEffect } from 'react';
import { Bars3Icon } from '@heroicons/react/24/outline';
import { api } from '../services/api';

interface HeaderProps {
    activeView: string;
    onMenuClick: () => void;
}

const Header: React.FC<HeaderProps> = ({ activeView, onMenuClick }) => {
    const [currentTime, setCurrentTime] = useState(new Date().toLocaleTimeString());

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

    return (
        <header className="h-16 border-b border-cyan/20 bg-[#050510]/80 backdrop-blur flex items-center justify-between px-6">
            <div className="flex items-center space-x-4">
                <button
                    className="text-cyan p-1 hover:bg-cyan/10 rounded mr-2"
                    onClick={onMenuClick}
                >
                    <Bars3Icon className="w-8 h-8" />
                </button>
                <div className="text-xl font-orbitron text-white tracking-widest uppercase truncate">
                    {activeView.replace('_', ' ')} <span className="text-cyan">///VIEW</span>
                </div>
            </div>

            <button
                className="btn-danger hidden md:block animate-pulse"
                onClick={handlePanic}
            >
                PANIC BUTTON
            </button>

            <div className="flex items-center space-x-6">
                <div className="text-right hidden sm:block">
                    <div className="text-[10px] text-gray-400">SYSTEM TIME</div>
                    <div className="text-lg font-mono text-white leading-none">{currentTime}</div>
                </div>
                <div className="h-8 w-[1px] bg-cyan/20 hidden sm:block" />
                <div className="flex items-center space-x-2">
                    <span className="h-2 w-2 bg-green-500 rounded-full animate-pulse" />
                    <span className="text-sm font-bold text-green-500 tracking-wider">ONLINE</span>
                </div>
            </div>
        </header>
    );
};

export default Header;
