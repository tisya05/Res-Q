import { useEffect, useRef, useState } from "react";
import SoundWave from "./SoundWave";


interface CallInterfaceProps {
  onEndCall: () => void;
}

export default function CallInterfaceClean({ onEndCall }: CallInterfaceProps) {
  const [isAISpeaking, setIsAISpeaking] = useState(false);
  const [response, setResponse] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [ipLocation, setIpLocation] = useState<string>("Fetching location...");

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
  const res = await fetch("http://127.0.0.1:5002/get_latest_response");
        if (!res.ok) return;
        const data = (await res.json()) as Record<string, any>;
        const last = data.last_response || data.reply || "";
        if (last) {
          setResponse(String(last));
          setIsAISpeaking(true);
          setTimeout(() => setIsAISpeaking(false), 1800);
        }
      } catch (e) {
        console.error("Polling error:", e);
      }
    }, 2500);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem("initial_ip_info");
      if (raw) {
        const info = JSON.parse(raw) as Record<string, any>;
        const geo = info.geolocation || info;
        const city = geo?.city || geo?.city_name || "";
        const region = geo?.regionName || geo?.region || "";
        const country = geo?.country || "";
        const parts = [city, region, country].filter(Boolean);
        if (parts.length) {
          setIpLocation(parts.join(", "));
          return;
        }
      }
    } catch {}

    (async () => {
      try {
  const res = await fetch("http://127.0.0.1:5002/get_ip");
        if (!res.ok) throw new Error("IP fetch failed");
        const data = (await res.json()) as Record<string, any>;
        const geo = data.geolocation || data.location_info || {};
        const city = geo.city || geo.city_name || "";
        const region = geo.regionName || geo.region || "";
        const country = geo.country || "";
        const publicIp = data.public_ip || data.ip || "";
        const parts = [city, region, country].filter(Boolean);
        setIpLocation(parts.length ? parts.join(", ") : publicIp || "Unknown location");
      } catch (e) {
        setIpLocation("Location unavailable");
      }
    })();
  }, []);

  const handleEnd = async () => {
    try {
  await fetch("http://127.0.0.1:5002/stop_listening", { method: "POST" });
    } catch (e) {
      console.error(e);
    }
    onEndCall();
  };

  const startRecording = async () => {
    try {
      // Tell backend to start a fresh listening session (clears convo state)
      try {
  await fetch("http://127.0.0.1:5002/start_listening", { method: "POST" });
      } catch (e) {
        // non-fatal
        console.warn("start_listening failed:", e);
      }

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      // Upload each chunk as it becomes available so server can transcribe in near-real-time
      mr.ondataavailable = async (ev) => {
        try {
          const chunk = ev.data;
          if (!chunk || chunk.size === 0) return;

          // also keep a local copy if needed
          chunksRef.current.push(chunk);

          const fd = new FormData();
          // use a consistent filename; server will convert to wav
          fd.append("audio", chunk, "chunk.webm");

          const res = await fetch("http://127.0.0.1:5002/upload_audio", { method: "POST", body: fd });
          if (!res.ok) {
            console.warn("chunk upload failed", res.status);
            return;
          }
          const data = await res.json();
          if (data.reply) setResponse(String(data.reply));
          if (data.audio_url) {
            try {
              const audio = new Audio(String(data.audio_url));
              await audio.play();
            } catch (e) {
              console.error("Failed to play audio", e);
            }
          }
        } catch (err) {
          console.error("ondataavailable upload error:", err);
        }
      };

      // Fallback upload when recording stops (ensures last few ms are sent)
      mr.onstop = async () => {
        try {
          if (chunksRef.current.length === 0) return;
          const blob = new Blob(chunksRef.current, { type: "audio/webm" });
          const fd = new FormData();
          fd.append("audio", blob, "recording_final.webm");
          const res = await fetch("http://127.0.0.1:5002/upload_audio", { method: "POST", body: fd });
          if (!res.ok) throw new Error("final upload failed");
          const data = await res.json();
          if (data.reply) setResponse(String(data.reply));
          if (data.audio_url) {
            try {
              const audio = new Audio(String(data.audio_url));
              await audio.play();
            } catch (e) {
              console.error("Failed to play audio", e);
            }
          }
        } catch (e) {
          console.error("final upload error:", e);
        } finally {
          // clear local chunks after finalization
          chunksRef.current = [];
        }
      };

      // Pass a timeslice so ondataavailable fires periodically (every 2.5s)
      mr.start(2500);
      mediaRecorderRef.current = mr;
      setIsRecording(true);
    } catch (e) {
      console.error(e);
    }
  };

  const stopRecording = () => {
    try {
      const mr = mediaRecorderRef.current;
      if (mr && mr.state !== "inactive") mr.stop();
    } catch (e) {
      console.error(e);
    }
    setIsRecording(false);
  };

  return (
    <div className="fixed inset-0 bg-black z-50 flex flex-col overflow-y-auto">
      <div className="flex-1 flex flex-col items-center justify-start pt-20 pb-8 px-6 space-y-10">
        <SoundWave isActive={isAISpeaking} />

        <div className="flex items-center gap-4">
          <button
            onClick={() => (isRecording ? stopRecording() : startRecording())}
            className={`px-6 py-3 rounded-full font-semibold transition-colors ${isRecording ? "bg-red-600 hover:bg-red-700" : "bg-green-600 hover:bg-green-700"}`}
          >
            {isRecording ? "Stop Recording" : "Record"}
          </button>
        </div>

        <div className="w-full max-w-4xl bg-gradient-to-br from-blue-900/40 to-blue-800/40 border-2 border-blue-500 rounded-3xl p-8 shadow-lg">
          <h2 className="text-white text-2xl font-bold mb-4">AI Response:</h2>
          <p className="text-gray-200 text-lg min-h-[100px] whitespace-pre-wrap">{response}</p>
        </div>

        <div className="w-full max-w-4xl bg-gradient-to-br from-gray-900/40 to-gray-800/40 border-2 border-gray-600 rounded-3xl p-8 shadow-lg">
          <h2 className="text-white text-2xl font-bold mb-4">Your Current Location:</h2>
          <p className="text-gray-300 text-lg">{ipLocation}</p>
        </div>

        <button
          onClick={handleEnd}
          className="bg-red-600 hover:bg-red-700 text-white font-semibold py-4 px-12 rounded-full text-lg transition-all duration-300 hover:scale-105 hover:shadow-2xl hover:shadow-red-500/50"
        >
          End Call
        </button>
      </div>
    </div>
  );
}
