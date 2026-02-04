import React, { useState } from 'react';
import { Upload, Check, AlertTriangle, UserPlus } from 'lucide-react';

import { api } from '../services/api';

const InsiderManagement = () => {
    const [name, setName] = useState('');
    const [file, setFile] = useState<File | null>(null);
    const [uploading, setUploading] = useState(false);
    const [status, setStatus] = useState<'idle' | 'success' | 'error'>('idle');
    const [message, setMessage] = useState('');

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files[0]) {
            setFile(e.target.files[0]);
            setStatus('idle');
        }
    };

    const handleUpload = async () => {
        if (!name || !file) {
            setStatus('error');
            setMessage('Please provide both a name and a photo.');
            return;
        }

        setUploading(true);
        setStatus('idle');

        try {
            const data = await api.uploadFace(name, file);

            if (data.status === 'success') {
                setStatus('success');
                setMessage(`Successfully added ${name} as an insider.`);
                setName('');
                setFile(null);
            } else {
                setStatus('error');
                setMessage(data.message || 'Upload failed.');
            }
        } catch (error: any) {
            setStatus('error');
            setMessage(error.message || 'Connection error. Is the backend running?');
        } finally {
            setUploading(false);
        }
    };

    return (
        <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-6 backdrop-blur-sm">
            <div className="flex items-center gap-3 mb-6">
                <UserPlus className="w-6 h-6 text-cyan-400" />
                <h3 className="text-xl font-bold text-slate-100">Add Insider Identity</h3>
            </div>

            <div className="space-y-4 max-w-md">
                {/* Name Input */}
                <div>
                    <label className="block text-slate-400 text-sm mb-2">Full Name</label>
                    <input
                        type="text"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="e.g. John Doe"
                        className="w-full bg-slate-800 border border-slate-600 rounded px-4 py-2 text-slate-100 focus:outline-none focus:border-cyan-500 transition-colors"
                    />
                </div>

                {/* File Input */}
                <div>
                    <label className="block text-slate-400 text-sm mb-2">Face Photo</label>
                    <div className="relative group">
                        <input
                            type="file"
                            accept="image/*"
                            onChange={handleFileChange}
                            id="face-upload"
                            className="hidden"
                        />
                        <label
                            htmlFor="face-upload"
                            className="flex items-center justify-center w-full p-4 border-2 border-dashed border-slate-600 rounded-lg cursor-pointer hover:border-cyan-500 hover:bg-slate-800/50 transition-all group-hover:text-cyan-400"
                        >
                            <div className="flex flex-col items-center gap-2 text-slate-400">
                                {file ? (
                                    <>
                                        <Check className="w-8 h-8 text-green-400" />
                                        <span className="text-green-400 font-medium">{file.name}</span>
                                    </>
                                ) : (
                                    <>
                                        <Upload className="w-8 h-8" />
                                        <span>Click to upload photo</span>
                                    </>
                                )}
                            </div>
                        </label>
                    </div>
                </div>

                {/* Status Message */}
                {status !== 'idle' && (
                    <div className={`p-3 rounded flex items-center gap-2 text-sm ${status === 'success' ? 'bg-green-500/10 text-green-400 border border-green-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'
                        }`}>
                        {status === 'success' ? <Check className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
                        {message}
                    </div>
                )}

                {/* Submit Button */}
                <button
                    onClick={handleUpload}
                    disabled={uploading}
                    className="w-full bg-cyan-600 hover:bg-cyan-500 text-white font-medium py-2 px-4 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                    {uploading ? (
                        <>
                            <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                            Uploading...
                        </>
                    ) : (
                        'Add Insider'
                    )}
                </button>
            </div>

            <div className="mt-6 p-4 bg-slate-800/50 rounded text-xs text-slate-400">
                <p className="font-semibold mb-1 text-slate-300">Tips for best results:</p>
                <ul className="list-disc list-inside space-y-1">
                    <li>Use a clear, well-lit photo of the face.</li>
                    <li>Ensure the face is looking directly at the camera.</li>
                    <li>Avoid sunglasses or heavy shadows.</li>
                    <li>You can upload multiple photos for the same person (use the same name).</li>
                </ul>
            </div>
        </div>
    );
};

export default InsiderManagement;
