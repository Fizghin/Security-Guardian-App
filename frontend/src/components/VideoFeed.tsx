import React, { useEffect, useRef, useState } from 'react';
import { api } from '../services/api';
import { SubtitleOverlay } from './SubtitleOverlay';

interface VideoFeedProps {
    lastAiMessage?: string;
    messageTime?: number;
}

const VideoFeed: React.FC<VideoFeedProps> = ({ lastAiMessage, messageTime }) => {
    const imgRef = useRef<HTMLImageElement>(null);
    const [isConnected, setIsConnected] = useState(false);
    const [fps, setFps] = useState(0);
    const framesRef = useRef(0);
    const previousUrlRef = useRef<string | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const isMountedRef = useRef(true);

    useEffect(() => {
        isMountedRef.current = true;
        let reconnectTimeout: ReturnType<typeof setTimeout>;

        const connect = () => {
            if (!isMountedRef.current) return;

            console.log("Attempting to connect to Video Stream...");
            const ws = new WebSocket(api.getStreamUrl());
            wsRef.current = ws;

            ws.onopen = () => {
                if (isMountedRef.current) {
                    console.log("Connected to Video Stream");
                    setIsConnected(true);
                }
            };

            ws.onmessage = (event) => {
                if (!isMountedRef.current) return;

                if (typeof event.data !== 'string') {
                    const blob = new Blob([event.data], { type: 'image/jpeg' });
                    if (imgRef.current) {
                        const url = URL.createObjectURL(blob);
                        if (previousUrlRef.current) {
                            URL.revokeObjectURL(previousUrlRef.current);
                        }
                        previousUrlRef.current = url;
                        imgRef.current.src = url;
                    }
                    framesRef.current++;
                }
            };

            ws.onclose = () => {
                if (isMountedRef.current) {
                    console.warn("Video Stream Disconnected. Retrying in 3s...");
                    setIsConnected(false);
                    reconnectTimeout = setTimeout(connect, 3000);
                }
            };

            ws.onerror = (err) => {
                console.error("WebSocket Error:", err);
                ws.close();
            };
        };

        connect();

        const fpsInterval = setInterval(() => {
            if (isMountedRef.current) {
                setFps(framesRef.current);
                framesRef.current = 0;
            }
        }, 1000);

        return () => {
            isMountedRef.current = false;
            if (wsRef.current) {
                wsRef.current.onclose = null; // Prevent reconnect on unmount
                wsRef.current.close();
            }
            clearTimeout(reconnectTimeout);
            clearInterval(fpsInterval);
            if (previousUrlRef.current) {
                URL.revokeObjectURL(previousUrlRef.current);
            }
        };
    }, []);

    return (
        <div className="relative w-full h-full rounded-lg overflow-hidden border border-cyan/50 bg-black">
            <div className="absolute top-0 left-0 w-full p-2 bg-black/80 z-10 flex justify-between items-center">
                <div className="flex items-center gap-2">
                    <div className={`w-3 h-3 rounded-full ${isConnected ? 'bg-green-500 shadow-[0_0_10px_#00ff00]' : 'bg-red-500 animate-pulse'}`} />
                    <span className="text-xs font-mono text-cyan tracking-wider">LIVE FEED :: CAMERA 01</span>
                </div>
                <div className="text-xs font-mono text-cyan/70">
                    FPS: {fps}
                </div>
            </div>

            <img
                ref={imgRef}
                alt="Live Stream"
                className="w-full h-full object-contain"
                style={{ minHeight: '300px', backgroundColor: '#000' }}
            />

            <SubtitleOverlay text={lastAiMessage} timestamp={messageTime} />
        </div>
    );
};

export default VideoFeed;
