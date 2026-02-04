import React, { useState } from 'react';
import SciFiCard from './SciFiCard';
import InsiderManagement from './InsiderManagement';
import { api } from '../services/api';

const SystemConfig: React.FC = () => {
    const [model, setModel] = useState('llama3');

    const handleUpdateConfig = async () => {
        try {
            await api.updateConfig({ model });
            alert("SYSTEM CONFIGURATION UPDATED.");
        } catch (err) {
            console.error(err);
        }
    };

    return (
        <div className="h-full grid grid-cols-12 gap-6 overflow-y-auto custom-scrollbar">
            {/* Camera Management */}
            <div className="col-span-12 lg:col-span-6">
                <SciFiCard title="CAMERA CONFIGURATION" color="cyan" className="h-full">
                    <div className="space-y-4">
                        {['Front Gate (CAM_01)', 'Lobby (CAM_02)', 'Server Room (CAM_03)'].map((cam, i) => (
                            <div key={i} className="flex justify-between items-center p-3 border border-gray-700 bg-black/40 rounded">
                                <div className="flex items-center gap-3">
                                    <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                                    <span className="text-white font-mono text-sm">{cam}</span>
                                </div>
                                <div className="flex gap-2">
                                    <button className="text-xs border border-cyan/30 text-cyan px-2 py-1 rounded hover:bg-cyan/10">ZONES</button>
                                    <button className="text-xs border border-red-500/30 text-red-500 px-2 py-1 rounded hover:bg-red-500/10">DISABLE</button>
                                </div>
                            </div>
                        ))}

                        <div className="p-4 border border-dashed border-gray-700 rounded bg-black/20">
                            <h4 className="text-gray-400 font-mono text-xs mb-3 uppercase">Add New Video Source</h4>
                            <div className="space-y-3">
                                <input type="text" placeholder="Device ID (0) or RTSP URL" className="w-full text-xs font-mono" />
                                <input type="text" placeholder="Camera Name (e.g., Back Hallway)" className="w-full text-xs font-mono" />
                                <button className="btn-primary w-full text-xs">CONNECT STREAM</button>
                            </div>
                        </div>
                    </div>
                </SciFiCard>
            </div>

            {/* AI Settings */}
            <div className="col-span-12 lg:col-span-6">
                <SciFiCard title="AI PROCESSING SETTINGS" color="magenta" className="h-full">
                    <div className="space-y-6 text-sm">
                        {/* New Connection Settings */}
                        <div className="bg-magenta/5 border border-magenta/20 p-4 rounded">
                            <h4 className="text-magenta font-bold mb-3 text-xs tracking-wider">AI PROVIDER CONNECTION</h4>
                            <div className="space-y-3">
                                <div>
                                    <label className="block text-gray-400 mb-1 text-[10px] uppercase">Service URL (Ollama/OpenAI)</label>
                                    <input type="text" defaultValue="http://localhost:11434" className="w-full text-xs font-mono bg-black/50 border-gray-600 focus:border-magenta" />
                                </div>
                                <div>
                                    <label className="block text-gray-400 mb-1 text-[10px] uppercase">Model Name</label>
                                    <select
                                        value={model}
                                        onChange={(e) => setModel(e.target.value)}
                                        className="w-full text-xs font-mono bg-black/50 border-gray-600 focus:border-magenta text-white p-2 rounded"
                                    >
                                        <option value="llama3">llama3</option>
                                        <option value="mistral">mistral</option>
                                        <option value="gpt-4o">gpt-4o</option>
                                        <option value="custom">custom</option>
                                    </select>
                                </div>
                                <button
                                    onClick={handleUpdateConfig}
                                    className="w-full py-2 bg-magenta/10 border border-magenta/50 text-magenta rounded hover:bg-magenta/20 text-xs font-bold font-orbitron"
                                >
                                    TEST CONNECTION
                                </button>
                            </div>
                        </div>

                        <div>
                            <label className="block text-gray-400 mb-2 font-mono">CONFIDENCE THRESHOLD</label>
                            <input type="range" className="w-full accent-magenta" min="0" max="100" defaultValue="65" />
                            <div className="flex justify-between text-[10px] text-gray-500 font-mono mt-1">
                                <span>AGGRESSIVE (0%)</span>
                                <span>STRICT (100%)</span>
                            </div>
                        </div>

                        <div className="flex items-center justify-between p-3 border border-magenta/30 bg-magenta/5 rounded">
                            <span className="text-magenta font-bold">NIGHT VISION ENHANCEMENT</span>
                            <div className="w-10 h-5 bg-magenta/20 rounded-full relative cursor-pointer">
                                <div className="absolute right-0.5 top-0.5 w-4 h-4 bg-magenta rounded-full shadow-[0_0_10px_#ff00ff]" />
                            </div>
                        </div>
                    </div>
                </SciFiCard>
            </div>

            {/* Insider Management */}
            <div className="col-span-12 lg:col-span-6">
                <InsiderManagement />
            </div>

            {/* System Health */}
            <div className="col-span-12">
                <SciFiCard title="SYSTEM DIAGNOSTICS" color="electric">
                    <div className="grid grid-cols-4 gap-4 text-center">
                        {['CPU TEMPERATE: 45°C', 'GPU USAGE: 32%', 'NETWORK: 1.2 GB/s', 'STORAGE: 1.4TB FREE'].map((stat, i) => (
                            <div key={i} className="p-4 bg-black/40 border border-electric/20 rounded">
                                <div className="text-electric font-mono text-xs tracking-wider">{stat}</div>
                            </div>
                        ))}
                    </div>
                </SciFiCard>
            </div>
        </div>
    );
};

export default SystemConfig;
