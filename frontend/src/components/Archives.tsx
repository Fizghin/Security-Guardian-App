import React, { useState } from 'react';
import SciFiCard from './SciFiCard';
import { PlayCircleIcon } from '@heroicons/react/24/outline'; // Assumed import

const Archives: React.FC = () => {
    // Mock recordings
    const [recordings] = useState([
        { id: '1', date: '2026-02-01 14:30', camera: 'Front Gate', size: '142 MB', duration: '00:45', thumbnail: 'https://via.placeholder.com/150/000000/00f0ff?text=REC_01' },
        { id: '2', date: '2026-02-01 15:15', camera: 'Lobby', size: '84 MB', duration: '00:23', thumbnail: 'https://via.placeholder.com/150/000000/00f0ff?text=REC_02' },
        { id: '3', date: '2026-02-01 16:00', camera: 'Server Room', size: '210 MB', duration: '01:12', thumbnail: 'https://via.placeholder.com/150/000000/00f0ff?text=REC_03' },
        { id: '4', date: '2026-02-01 10:20', camera: 'Parking Lot', size: '1.2 GB', duration: '45:00', thumbnail: 'https://via.placeholder.com/150/000000/00f0ff?text=REC_04' },
        { id: '5', date: '2026-01-31 23:11', camera: 'Front Gate', size: '22 MB', duration: '00:10', thumbnail: 'https://via.placeholder.com/150/000000/00f0ff?text=REC_05' },
    ]);

    return (
        <div className="h-full flex flex-col gap-6">
            <div className="flex justify-between items-center">
                <SciFiCard title="ARCHIVE FILTERS" color="cyan" className="w-full">
                    <div className="flex gap-4">
                        <input type="date" className="glass-panel text-white p-2 rounded w-48" />
                        <select className="glass-panel text-white p-2 rounded w-48">
                            <option>All Cameras</option>
                            <option>Front Gate</option>
                            <option>Lobby</option>
                        </select>
                        <button className="btn-primary">APPLY FILTER</button>
                    </div>
                </SciFiCard>
            </div>

            <SciFiCard title="RECORDING DATABASE" color="electric" className="flex-1 overflow-hidden">
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 h-full overflow-y-auto custom-scrollbar p-2">
                    {recordings.map((rec) => (
                        <div key={rec.id} className="glass-panel p-3 rounded group hover:bg-white/5 transition-all relative">
                            <div className="aspect-video bg-black/50 rounded mb-2 relative overflow-hidden">
                                <img src={rec.thumbnail} alt="Thumbnail" className="w-full h-full object-cover opacity-60 group-hover:opacity-100 transition-opacity" />
                                <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                                    <PlayCircleIcon className="w-12 h-12 text-cyan drop-shadow-[0_0_10px_rgba(0,240,255,0.8)]" />
                                </div>
                                <div className="absolute bottom-1 right-1 text-[10px] bg-black/80 text-white px-1 rounded font-mono">
                                    {rec.duration}
                                </div>
                            </div>
                            <div className="flex justify-between items-start">
                                <div>
                                    <div className="text-cyan font-bold text-sm tracking-wide">{rec.camera.toUpperCase()}</div>
                                    <div className="text-gray-400 text-xs font-mono">{rec.date}</div>
                                </div>
                                <div className="text-gray-500 text-[10px] font-mono border border-gray-700 px-1 rounded">
                                    {rec.size}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </SciFiCard>
        </div>
    );
};

export default Archives;
