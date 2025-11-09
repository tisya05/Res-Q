import { Phone } from "lucide-react";

interface HeroSectionProps {
  onCallClick: () => void;
}

export default function HeroSection({ onCallClick }: HeroSectionProps) {
  // --- Wrapper to call backend before showing CallInterface ---
  const handleCallClick = async () => {
    try {
      await fetch("http://127.0.0.1:5001/start_listening", { method: "POST" });
    } catch (err) {
      console.error("Failed to start listening:", err);
    }
    onCallClick(); // existing logic to show CallInterface
  };

  return (
    <section
      id="home"
      className="min-h-screen bg-black flex items-center justify-center relative overflow-hidden pt-20"
    >
      {/* Left Image (AI Head Graphic) */}
      <img
        src="/assets/ai.jpg"
        alt="AI Illustration"
        className="absolute left-8 bottom-10 w-72 md:w-[24rem] opacity-90 select-none"
      />

      {/* Right Image (Megaphone Graphic) */}
      <img
        src="/assets/megaphone.jpg"
        alt="Megaphone Illustration"
        className="absolute right-[-1rem] bottom-6 w-56 md:w-72 opacity-90 select-none"
      />

      {/* Main Content */}
      <div className="text-center z-10 px-6">
        <h1 className="text-7xl md:text-8xl font-bold mb-6 leading-tight">
          <span className="text-white block">Get Immediate</span>
          <span className="text-blue-600 block">ASSISTANCE</span>
        </h1>

        {/* TALK button */}
        <button
          onClick={handleCallClick}
          className="group relative mt-12 mx-auto flex items-center justify-between gap-6
                     px-12 py-5 rounded-full text-xl font-semibold tracking-wide
                     text-white border border-[#6C4BFF] 
                     bg-[radial-gradient(circle_at_left,_#1E003C,_#4C3CF3_70%,_#221047_100%)]
                     transition-all duration-300 hover:scale-105 hover:shadow-lg hover:shadow-[#6C4BFF]/50"
        >
          <span className="ml-2">TALK TO RES-Q</span>
          <div
            className="flex items-center justify-center bg-black rounded-full w-10 h-10 
                        group-hover:bg-[#1A003C] transition-colors duration-300"
          >
            <Phone className="w-5 h-5 text-white" />
          </div>
        </button>

        <p className="text-white text-xl mt-8 tracking-wide">
          POWERED BY AI. GET REAL-TIME HELP IN ANY LANGUAGE.
        </p>
      </div>
    </section>
  );
}
