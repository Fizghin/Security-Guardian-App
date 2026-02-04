const BASE_URL = 'http://127.0.0.1:8000';
const WS_URL = 'ws://127.0.0.1:8000/ws/stream';

export interface SystemStats {
    total_events: number;
    active_threats: number;
    threat_level: number;
    system_status: string;
    ai_personality: {
        intimidation: number;
        humor: number;
        persistence: number;
    };
    last_ai_message?: string;
    message_time?: number;
}

export interface SecurityEvent {
    id: number;
    timestamp: string;
    event_type: string;
    description: string;
    severity: string;
}

export const api = {
    async getStats(): Promise<SystemStats> {
        const res = await fetch(`${BASE_URL}/api/stats`);
        if (!res.ok) throw new Error('Failed to fetch stats');
        return res.json();
    },

    async triggerPanic(): Promise<{ status: string }> {
        const res = await fetch(`${BASE_URL}/api/panic`, { method: 'POST' });
        if (!res.ok) throw new Error('Panic protocol failed');
        return res.json();
    },

    async simulatePerson(): Promise<{ status: string }> {
        const res = await fetch(`${BASE_URL}/api/simulate/person`, { method: 'POST' });
        if (!res.ok) throw new Error('Simulation failed');
        return res.json();
    },

    async updateConfig(config: any): Promise<{ status: string }> {
        const res = await fetch(`${BASE_URL}/api/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        if (!res.ok) throw new Error('Config update failed');
        return res.json();
    },

    async getEvents(limit: number = 50): Promise<SecurityEvent[]> {
        const res = await fetch(`${BASE_URL}/api/events?limit=${limit}`);
        if (!res.ok) throw new Error('Failed to fetch events');
        return res.json();
    },

    async uploadFace(name: string, file: File): Promise<{ status: string, message: string }> {
        const formData = new FormData();
        formData.append('name', name);
        formData.append('file', file);

        const res = await fetch(`${BASE_URL}/api/faces/upload`, {
            method: 'POST',
            body: formData,
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.message || 'Upload failed');
        return data;
    },

    getStreamUrl(): string {
        return WS_URL;
    }
};
