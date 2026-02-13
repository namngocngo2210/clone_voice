import { useState, useEffect, useRef } from 'react';
import { invoke, convertFileSrc } from '@tauri-apps/api/tauri';
import { listen } from '@tauri-apps/api/event';
import { open } from '@tauri-apps/api/dialog';
import {
    Loader2, FolderOpen, Play, Cpu, Monitor,
    Terminal, Trash2, X, ExternalLink, Globe, Mic, Settings,
    Upload, FileText, Square
} from 'lucide-react';

const SUPPORTED_LANGUAGES = [
    { code: 'vi', name: 'Vietnamese (Tiếng Việt)' },
    { code: 'en', name: 'English' },
    { code: 'es', name: 'Spanish (Español)' },
    { code: 'fr', name: 'French (Français)' },
    { code: 'de', name: 'German (Deutsch)' },
    { code: 'it', name: 'Italian (Italiano)' },
    { code: 'pt', name: 'Portuguese (Português)' },
    { code: 'pl', name: 'Polish (Polski)' },
    { code: 'tr', name: 'Turkish (Türkçe)' },
    { code: 'ru', name: 'Russian (Русский)' },
    { code: 'nl', name: 'Dutch (Nederlands)' },
    { code: 'cs', name: 'Czech (Čeština)' },
    { code: 'ar', name: 'Arabic (العربية)' },
    { code: 'zh-cn', name: 'Chinese (中文)' },
    { code: 'ja', name: 'Japanese (日本語)' },
    { code: 'hu', name: 'Hungarian (Magyar)' },
    { code: 'ko', name: 'Korean (한국어)' },
    { code: 'hi', name: 'Hindi (हिन्दी)' },
];

function App() {
    // State
    const [text, setText] = useState('');
    const [speakerWav, setSpeakerWav] = useState('');
    const [language, setLanguage] = useState('vi');
    const [speed, setSpeed] = useState(1.0);
    const [temperature, setTemperature] = useState(0.75);
    const [pauseSentence, setPauseSentence] = useState(0.3);
    const [pauseParagraph, setPauseParagraph] = useState(0.8);
    const [exportSrt, setExportSrt] = useState(true);

    const [outputPath, setOutputPath] = useState('C:/Users/namng/OneDrive/Desktop/output');
    const [outputFilename, setOutputFilename] = useState('output_voice');

    const [loading, setLoading] = useState(false);
    const [logs, setLogs] = useState<string[]>([]);
    const [progress, setProgress] = useState(0);
    const [sysInfo, setSysInfo] = useState({ cpu: '...', gpu: '...' });
    const [deviceMode, setDeviceMode] = useState('gpu'); // Default to GPU
    const [generatedAudio, setGeneratedAudio] = useState<string | null>(null);

    const audioRef = useRef<HTMLAudioElement | null>(null);

    useEffect(() => {
        invoke('get_system_info').then(info => setSysInfo(info as any)).catch(e => console.error(e));

        const unlistenLog = listen('sidecar-log', (event) => {
            setLogs((prev) => [...prev.slice(-99), `${event.payload as string}`]);
            if ((event.payload as string).includes('Synthesizing')) setProgress(40);
            if ((event.payload as string).includes('Processing')) setProgress(prev => Math.min(prev + 5, 95));
        });
        const unlistenError = listen('sidecar-error', (event) => {
            setLogs((prev) => [...prev.slice(-99), `[LỖI] ${event.payload}`]);
            setLoading(false);
        });

        return () => {
            unlistenLog.then(f => f());
            unlistenError.then(f => f());
        };
    }, []);

    const handleSynthesize = async () => {
        if (!text || !speakerWav) {
            alert("Vui lòng nhập văn bản và chọn file giọng mẫu!");
            return;
        }

        setLoading(true);
        setProgress(5);
        setLogs(["Bắt đầu xử lý..."]);
        try {
            const result = await invoke<string>('run_synthesis', {
                params: {
                    text,
                    speaker_wav: speakerWav,
                    language,
                    speed,
                    temperature,
                    top_k: 50,
                    top_p: 0.85,
                    repetition_penalty: 5.0,
                    export_srt: exportSrt,
                    custom_output_path: outputPath,
                    output_filename: outputFilename,
                    device: deviceMode.includes('gpu') ? 'cuda' : 'cpu',
                    pause_sentence: pauseSentence,
                    pause_paragraph: pauseParagraph
                }
            });

            setLogs((prev) => [...prev, `[Xong] Đã lưu file: ${result}`]);
            setGeneratedAudio(result);
            setProgress(100);
        } catch (error) {
            setLogs((prev) => [...prev, `[Thông báo] ${error}`]);
        } finally {
            setLoading(false);
        }
    };

    const handleStop = async () => {
        try {
            await invoke('stop_synthesis');
            setLoading(false);
            setProgress(0);
            setLogs(prev => [...prev, "[Hệ thống] Đã dừng quá trình tạo Voice."]);
        } catch (e) {
            console.error(e);
        }
    };

    const handleSelectFolder = async () => {
        const selected = await open({ directory: true, multiple: false });
        if (selected && typeof selected === 'string') setOutputPath(selected);
    };

    const handlePickSpeakerWav = async () => {
        const selected = await open({
            multiple: false,
            filters: [{ name: 'Audio', extensions: ['wav', 'mp3', 'ogg'] }]
        });
        if (selected && typeof selected === 'string') setSpeakerWav(selected);
    };

    const handlePickTextFile = async () => {
        const selected = await open({
            multiple: false,
            filters: [{ name: 'Text', extensions: ['txt'] }]
        });
        if (selected && typeof selected === 'string') {
            try {
                const content = await invoke<string>('read_text_file', { path: selected });
                setText(content);
                setLogs(prev => [...prev, `[Hệ thống] Đã tải nội dung từ: ${selected.split('\\').pop()}`]);
            } catch (e) {
                alert("Không thể đọc file: " + e);
            }
        }
    };

    const handleOpenFolder = async () => {
        try { await invoke('open_folder', { path: outputPath }); }
        catch (e) { alert("Lỗi: " + e); }
    };

    const handlePlayAudio = () => {
        if (generatedAudio && audioRef.current) {
            audioRef.current.src = convertFileSrc(generatedAudio);
            audioRef.current.play();
        }
    };

    const handleCopyLogs = async () => {
        try {
            await navigator.clipboard.writeText(logs.join('\n'));
            const oldLogs = [...logs];
            setLogs(prev => [...prev, "[Hệ thống] Đã sao chép toàn bộ log vào clipboard."]);
            setTimeout(() => setLogs(oldLogs), 2000);
        } catch (err) {
            console.error('Lỗi khi copy log:', err);
        }
    };

    return (
        <div className="flex flex-col h-screen w-full bg-slate-50 text-slate-700 font-sans p-3 overflow-hidden select-none" style={{ fontFamily: "'Inter', system-ui, sans-serif" }}>
            <div className="flex-1 grid grid-cols-12 gap-3 overflow-hidden h-full">

                {/* LEFT COLUMN: Editor, Voice & IO */}
                <div className="col-span-12 lg:col-span-7 flex flex-col gap-3 overflow-hidden h-full">
                    {/* Editor Area */}
                    <div className="flex-[4] bg-white border border-slate-200 rounded-lg flex flex-col overflow-hidden shadow-sm">
                        <div className="px-3 py-1 border-b border-slate-100 flex items-center justify-between bg-zinc-50/50">
                            <span className="text-[10px] font-bold text-slate-400 flex items-center gap-2">
                                <FileText size={12} /> Nội dung văn bản ({text.length} kí tự)
                            </span>
                            <div className="flex gap-3">
                                <button onClick={handlePickTextFile} className="text-[10px] font-bold text-emerald-600 hover:text-emerald-700 flex items-center gap-1"><Upload size={10} /> Tải file .txt</button>
                                <button onClick={async () => setText(await navigator.clipboard.readText())} className="text-[10px] font-bold text-blue-500 hover:text-blue-600">Dán từ Clipboard</button>
                                <button onClick={() => setText('')} className="text-[10px] font-bold text-red-500 hover:text-red-600">Xóa sạch</button>
                            </div>
                        </div>
                        <textarea
                            className="flex-1 p-3 text-base leading-relaxed outline-none resize-none placeholder:text-slate-200 text-slate-600"
                            placeholder="Nhập nội dung cần chuyển sang giọng nói..."
                            value={text}
                            onChange={(e) => setText(e.target.value)}
                        />
                    </div>

                    {/* Voice Selection */}
                    <div className="bg-white border border-slate-200 rounded-lg p-2.5 space-y-1.5 shadow-sm shrink-0">
                        <label className="text-[9px] font-bold text-slate-400 flex items-center gap-1"><Mic size={10} /> Chọn giọng mẫu (.wav)</label>
                        <div className="flex gap-2">
                            <input
                                readOnly
                                onClick={handlePickSpeakerWav}
                                className="flex-1 bg-slate-50 border border-slate-200 rounded px-3 py-1 text-xs font-bold text-slate-500 outline-none cursor-pointer truncate"
                                value={speakerWav ? speakerWav.split('\\').slice(-1)[0] : 'Nhấp để chọn file ghi âm mẫu...'}
                            />
                            <button onClick={handlePickSpeakerWav} className="px-4 py-1.5 bg-white border border-slate-200 rounded text-[10px] font-bold text-slate-500 hover:bg-slate-50">Chọn file</button>
                            {speakerWav && <button onClick={() => setSpeakerWav('')} className="p-1 px-2 border border-slate-200 rounded text-red-400 hover:bg-red-50"><X size={14} /></button>}
                        </div>
                    </div>

                    {/* Output Control */}
                    <div className="bg-white border border-slate-200 rounded-lg p-2.5 space-y-2.5 shadow-sm shrink-0">
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <label className="text-[9px] font-bold text-slate-400">Tên file kết quả</label>
                                <input
                                    type="text"
                                    className="w-full bg-slate-50 border border-slate-200 rounded px-3 py-1 text-xs font-bold text-slate-600 outline-none focus:border-blue-300"
                                    value={outputFilename}
                                    onChange={(e) => setOutputFilename(e.target.value)}
                                />
                            </div>
                            <div className="space-y-1">
                                <label className="text-[9px] font-bold text-slate-400 flex justify-between">
                                    Vị trí lưu
                                    <button onClick={handleOpenFolder} className="text-blue-500 hover:underline flex items-center gap-1 lowercase text-[8px] font-normal !normal-case opacity-70"><ExternalLink size={8} /> mở thư mục</button>
                                </label>
                                <div className="flex gap-1">
                                    <input readOnly onClick={handleSelectFolder} type="text" className="flex-1 bg-slate-50 border border-slate-200 rounded px-3 py-1 text-[10px] text-slate-400 cursor-pointer truncate outline-none" value={outputPath} />
                                    <button onClick={handleSelectFolder} className="px-2 bg-white border border-slate-200 rounded text-slate-500 hover:bg-slate-50"><FolderOpen size={14} /></button>
                                </div>
                            </div>
                        </div>

                        <div className="space-y-2.5">
                            <div className="relative w-full h-4 bg-slate-100 rounded overflow-hidden border border-slate-200">
                                <div className="absolute left-0 top-0 h-full bg-blue-500 transition-all duration-700" style={{ width: `${progress}%` }} />
                                <span className="absolute inset-0 flex items-center justify-center text-[8px] font-bold text-slate-600 mix-blend-difference tracking-wider">
                                    Tiến trình: {progress}%
                                </span>
                            </div>

                            <div className="flex gap-2">
                                <button onClick={() => audioRef.current?.pause()} className="px-5 py-2 bg-white border border-slate-200 rounded text-[10px] font-bold text-slate-500 hover:bg-red-50 hover:text-red-500 transition-colors">
                                    Dừng phát
                                </button>

                                {loading ? (
                                    <button
                                        onClick={handleStop}
                                        className="flex-1 py-2 bg-red-500 text-white rounded font-black text-[11px] hover:bg-red-600 transition-all flex items-center justify-center gap-2 shadow-sm"
                                    >
                                        <Square size={12} fill="currentColor" /> Dừng tạo
                                    </button>
                                ) : (
                                    <button
                                        onClick={handleSynthesize}
                                        className="flex-1 py-2 bg-white border border-blue-500 text-blue-500 rounded font-black text-[11px] hover:bg-blue-500 hover:text-white transition-all flex items-center justify-center gap-2 shadow-sm"
                                    >
                                        Bắt đầu tạo Voice
                                    </button>
                                )}
                            </div>
                            {generatedAudio && (
                                <button onClick={handlePlayAudio} className="w-full text-center text-[9px] font-black text-blue-500 hover:underline flex items-center justify-center gap-1 tracking-tight py-0.5 bg-blue-50/30 rounded">
                                    <Play size={10} fill="currentColor" /> Nghe lại tệp vừa tạo
                                </button>
                            )}
                        </div>
                    </div>
                </div>

                {/* RIGHT COLUMN: Parameters & Monitor */}
                <div className="col-span-12 lg:col-span-5 flex flex-col gap-3 overflow-hidden h-full">

                    {/* Terminal */}
                    <div className="flex-[1.8] bg-[#0f172a] border border-slate-800 rounded-lg flex flex-col overflow-hidden shadow-xl">
                        <div className="px-2.5 py-1 bg-slate-800/80 flex items-center justify-between text-[8px] font-black text-slate-500 uppercase tracking-widest border-b border-slate-700/50">
                            <span className="flex items-center gap-1.5"><Terminal size={10} className="text-blue-400" /> Logs</span>
                            <div className="flex gap-3">
                                <button onClick={handleCopyLogs} className="hover:text-blue-400 transition-opacity font-bold">Sao chép</button>
                                <button onClick={() => setLogs([])} className="hover:text-white transition-opacity font-bold">Dọn dẹp</button>
                            </div>
                        </div>
                        <div className="flex-1 p-2.5 overflow-y-auto font-mono text-[9px] space-y-0.5 custom-scrollbar leading-tight">
                            {logs.map((log, i) => (
                                <div key={i} className={log.includes('[LỖI]') ? 'text-rose-400' : 'text-blue-300'}>
                                    <span className="text-slate-600 mr-2 opacity-50">[{new Date().toLocaleTimeString('en-GB')}]</span>
                                    {log}
                                </div>
                            ))}
                            {logs.length === 0 && <div className="text-slate-700 italic opacity-40 py-1">Hệ thống sẵn sàng...</div>}
                        </div>
                    </div>

                    {/* Navigation */}
                    <div className="flex border-b border-slate-200 gap-6 px-1 shrink-0">
                        <button className="pb-1 text-[10px] font-black transition-all border-b-2 border-blue-500 text-blue-600">Cấu hình AI</button>
                    </div>

                    {/* Settings Area */}
                    <div className="flex-[4] bg-white border border-slate-200 rounded-lg p-4 overflow-y-auto space-y-5 shadow-sm no-scrollbar">

                        <div className="space-y-1.5">
                            <label className="text-[10px] font-bold text-slate-400 flex items-center gap-1 tracking-wider"><Globe size={11} /> Ngôn ngữ đầu ra</label>
                            <select
                                className="w-full bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs font-bold text-slate-600 outline-none cursor-pointer focus:border-blue-300 transition-all shadow-sm"
                                value={language}
                                onChange={e => setLanguage(e.target.value)}
                            >
                                {SUPPORTED_LANGUAGES.map(lang => (
                                    <option key={lang.code} value={lang.code}>{lang.name}</option>
                                ))}
                            </select>
                        </div>

                        <div className="space-y-4">
                            <div className="space-y-3">
                                <div className="flex justify-between items-center">
                                    <label className="text-[10px] font-bold text-slate-400 tracking-wider flex gap-2 items-center">Tốc độ đọc: <span className="text-blue-600 font-bold">{speed}x</span></label>
                                    <button onClick={() => setSpeed(1.0)} className="text-[9px] text-blue-500 hover:underline font-bold">Mặc định</button>
                                </div>
                                <input type="range" min="0.5" max="2.0" step="0.1" value={speed} onChange={e => setSpeed(parseFloat(e.target.value))} className="w-full accent-blue-500 h-1.5 bg-slate-100 rounded-full appearance-none cursor-pointer" />

                                <div className="flex justify-between items-center pt-1">
                                    <label className="text-[10px] font-bold text-slate-400 tracking-wider flex gap-2 items-center">Độ biến hóa: <span className="text-blue-600 font-bold">{temperature}</span></label>
                                    <button onClick={() => setTemperature(0.75)} className="text-[9px] text-blue-500 hover:underline font-bold">Mặc định</button>
                                </div>
                                <input type="range" min="0.1" max="1.0" step="0.05" value={temperature} onChange={e => setTemperature(parseFloat(e.target.value))} className="w-full accent-blue-500 h-1.5 bg-slate-100 rounded-full appearance-none cursor-pointer" />
                            </div>

                            <div className="grid grid-cols-2 gap-3">
                                <div className="bg-slate-50 border border-slate-100 p-2.5 rounded-lg flex flex-col gap-0.5 shadow-sm">
                                    <span className="text-[9px] font-bold text-slate-400">Ngắt nghỉ câu (s)</span>
                                    <input type="number" step="0.1" className="bg-transparent font-bold text-sm outline-none text-blue-600" value={pauseSentence} onChange={e => setPauseSentence(parseFloat(e.target.value))} />
                                </div>
                                <div className="bg-slate-50 border border-slate-100 p-2.5 rounded-lg flex flex-col gap-0.5 shadow-sm">
                                    <span className="text-[9px] font-bold text-slate-400">Ngắt nghỉ đoạn (s)</span>
                                    <input type="number" step="0.1" className="bg-transparent font-bold text-sm outline-none text-blue-600" value={pauseParagraph} onChange={e => setPauseParagraph(parseFloat(e.target.value))} />
                                </div>
                            </div>
                        </div>

                        <div className="pt-3 border-t border-slate-100 space-y-3">
                            <label className="text-[10px] font-bold text-slate-400 flex items-center justify-center gap-1 tracking-[0.15em]">Sử dụng phần cứng</label>
                            <div className="flex gap-10 items-center justify-center">
                                <label className="flex items-center gap-3 cursor-pointer group">
                                    <input type="radio" className="w-3.5 h-3.5 accent-blue-500" checked={deviceMode === 'gpu'} onChange={() => setDeviceMode('gpu')} />
                                    <span className={`text-[12px] font-black tracking-tight ${deviceMode === 'gpu' ? 'text-blue-600' : 'text-slate-400'}`}>Xử lý GPU</span>
                                </label>
                                <label className="flex items-center gap-3 cursor-pointer group">
                                    <input type="radio" className="w-3.5 h-3.5 accent-blue-500" checked={deviceMode === 'cpu'} onChange={() => setDeviceMode('cpu')} />
                                    <span className={`text-[12px] font-black tracking-tight ${deviceMode === 'cpu' ? 'text-slate-600' : 'text-slate-400'}`}>Sử dụng CPU</span>
                                </label>
                            </div>
                        </div>

                        <div className="bg-slate-900 border border-slate-800 rounded-lg p-2.5 space-y-2 mt-2 shadow-lg">
                            <div className="flex justify-between items-center border-b border-slate-800 pb-1 mb-1 opacity-60">
                                <span className="text-[8px] font-bold text-slate-500 uppercase">Hardware Monitor</span>
                                <span className="text-[8px] text-blue-400 font-bold uppercase truncate max-w-[120px]">{sysInfo.gpu.split(' ').slice(0, 2).join(' ')}</span>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="flex flex-col">
                                    <span className="text-[8px] text-slate-600 font-bold">CPU LOAD</span>
                                    <div className="h-1 bg-slate-800 rounded-full mt-1"><div className="h-full bg-slate-600 w-1/4 rounded-full" /></div>
                                </div>
                                <div className="flex flex-col">
                                    <span className="text-[8px] text-slate-600 font-bold">GPU LOAD</span>
                                    <div className="h-1 bg-slate-800 rounded-full mt-1"><div className="h-full bg-blue-500 w-1/3 rounded-full" /></div>
                                </div>
                            </div>
                        </div>

                        <button className="w-full py-2.5 bg-slate-50 text-slate-400 font-bold text-[9px] rounded-lg border border-slate-100 hover:bg-slate-100 transition-all flex items-center justify-center gap-2 tracking-[0.1em] h-10 mt-auto shrink-0 shadow-sm"><Settings size={11} /> Hướng dẫn & Hỗ trợ</button>
                    </div>

                </div>
            </div>
            <audio ref={audioRef} className="hidden" />
        </div>
    );
}

export default App;
