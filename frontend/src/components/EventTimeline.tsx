import React, { useEffect, useState } from 'react';
import SciFiCard from './SciFiCard';
import { api } from '../services/api';
import type { SecurityEvent } from '../services/api';

const EventTimeline: React.FC = () => {
    const [events, setEvents] = useState<SecurityEvent[]>([]);

    useEffect(() => {
        const fetchEvents = async () => {
            try {
                const data = await api.getEvents();
                setEvents(data);
            } catch (err) {
                console.error(err);
            }
        };
        fetchEvents();
        const interval = setInterval(fetchEvents, 2000);
        return () => clearInterval(interval);
    }, []);

    const formatTime = (isoString: string) => {
        return new Date(isoString).toLocaleTimeString('en-GB', { hour12: false });
    };

    return (
        <SciFiCard title="EVENT LOG" className="h-full overflow-hidden flex flex-col" color="cyan">
            <div className="overflow-y-auto pr-2 space-y-3 custom-scrollbar flex-1">
                {events.map((event) => (
                    <div key={event.id} className={`p-3 rounded border-l-2 bg-black/40 ${event.severity === 'CRITICAL' || event.severity === 'HIGH' ? 'border-magenta bg-magenta/5' :
                        event.severity === 'MEDIUM' ? 'border-electric bg-electric/5' :
                            'border-cyan bg-cyan/5'
                        }`}>
                        <div className="flex justify-between items-start mb-1">
                            <span className={`text-[10px] font-bold px-1 rounded ${event.severity === 'CRITICAL' || event.severity === 'HIGH' ? 'bg-magenta text-black' :
                                event.severity === 'MEDIUM' ? 'bg-electric text-black' :
                                    'bg-cyan text-black'
                                }`}>
                                {event.event_type}
                            </span>
                            <span className="text-xs font-mono text-gray-500">{formatTime(event.timestamp)}</span>
                        </div>
                        <p className="text-sm font-rajdhani">{event.description}</p>
                    </div>
                ))}
            </div>
        </SciFiCard>
    );
};

export default EventTimeline;
