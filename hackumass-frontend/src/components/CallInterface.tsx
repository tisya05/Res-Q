import { useEffect, useState } from "react";
import SoundWave from "./SoundWave";

interface CallInterfaceProps {
  onEndCall: () => void;
}

export default function CallInterface({ onEndCall }: CallInterfaceProps) {
  const [isAISpeaking, setIsAISpeaking] = useState(false);
  const [response, setResponse] = useState("");
  const [userLocation, setUserLocation] = useState<string>("Fetching location...");

  // --- Poll Flask backend for AI responses ---
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch("http://127.0.0.1:5001/get_latest_response");
        if (!res.ok) throw new Error("Failed to fetch latest response");
        const data = await res.json();

        if (data.last_response) {
          setResponse(data.last_response);
          setIsAISpeaking(true);
          // Reset speaking state after a short delay
          setTimeout(() => setIsAISpeaking(false), 2000);
        }

        if (data.location_info) {
          setUserLocation(data.location_info);
        }
      } catch (err) {
        console.error("Polling failed:", err);
      }
    }, 2500);

    return () => clearInterval(interval);
  }, []);

  // --- Handle call end ---
  const handleEnd = async () => {
    try {
      await fetch("http://127.0.0.1:5001/stop_listening", { method: "POST" });
    } catch (err) {
      console.error("Error stopping listening:", err);
    }
    onEndCall();
  };

  return (
    <div className="fixed inset-0 bg-black z-50 flex flex-col overflow-y-auto">
      <div className="flex-1 flex flex-col items-center justify-start pt-20 pb-8 px-6 space-y-10">
        {/* Sound wave */}
        <SoundWave isActive={isAISpeaking} />

        {/* AI Response */}
        <div className="w-full max-w-4xl bg-gradient-to-br from-blue-900/40 to-blue-800/40 border-2 border-blue-500 rounded-3xl p-8 shadow-lg">
          <h2 className="text-white text-2xl font-bold mb-4">AI Response:</h2>
          <p className="text-gray-200 text-lg min-h-[100px]">{response}</p>
        </div>

        {/* User Location */}
        <div className="w-full max-w-4xl bg-gradient-to-br from-gray-900/40 to-gray-800/40 border-2 border-gray-600 rounded-3xl p-8 shadow-lg">
          <h2 className="text-white text-2xl font-bold mb-4">Your Current Location:</h2>
          <p className="text-gray-300 text-lg">{userLocation}</p>
        </div>

        {/* End Call Button */}
        <button
          onClick={handleEnd}
          className="bg-blue-600 hover:bg-blue-700 text-white font-semibold py-4 px-12 rounded-full text-lg transition-all duration-300 hover:scale-105 hover:shadow-2xl hover:shadow-blue-500/50"
        >
          End Call
        </button>
      </div>
    </div>
  );
}
