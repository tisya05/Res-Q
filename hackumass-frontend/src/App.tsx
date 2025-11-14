import { useState } from "react";
import Header from "./components/Header";
import HeroSection from "./components/HeroSection";
import LocationConsentModal from "./components/LocationConsentModal";
import CallInterface from "./components/CallInterfaceClean";
import UpdatesSection from "./components/UpdatesSection";
import AboutSection from "./components/AboutSection";

function App() {
  const [showConsentModal, setShowConsentModal] = useState(false);
  const [isCallActive, setIsCallActive] = useState(false);

  const handleCallClick = () => {
    setShowConsentModal(true);
  };

  const handleConsent = () => {
    setShowConsentModal(false);
    setIsCallActive(true); // Show CallInterface
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleDecline = () => {
    setShowConsentModal(false);
  };

  const handleEndCall = () => {
    setIsCallActive(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleNavigate = (section: string) => {
    const element = document.getElementById(section);
    if (element) element.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className="min-h-screen bg-black">
      <Header onNavigate={handleNavigate} isCallActive={isCallActive} />

      {isCallActive ? (
        <CallInterface onEndCall={handleEndCall} />
      ) : (
        <>
          <HeroSection onCallClick={handleCallClick} />
          <UpdatesSection />
          <AboutSection />
        </>
      )}

      <LocationConsentModal
        isOpen={showConsentModal}
        onConsent={handleConsent}
        onDecline={handleDecline}
      />
    </div>
  );
}

export default App;
