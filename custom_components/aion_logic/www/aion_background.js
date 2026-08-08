/* Aion Logic™ - Autonome Dynamische Achtergrond Engine */
console.info("%c AION LOGIC: Dynamic Background Engine initialized ", "color: #78b7bb; font-weight: bold; background: #161616; padding: 4px; border-radius: 4px;");

function setAionBackground(url) {
    if (!url || url.includes("unavailable") || url.includes("unknown")) return;
    
    // Oplossing voor mobiele WebViews: vaste div i.p.v. background-attachment op body
    let bgDiv = document.getElementById("aion-dynamic-bg");
    if (!bgDiv) {
        bgDiv = document.createElement("div");
        bgDiv.id = "aion-dynamic-bg";
        Object.assign(bgDiv.style, {
            position: "fixed", top: "0", left: "0",
            width: "100vw", height: "100vh", zIndex: "-1",
            backgroundSize: "cover", backgroundPosition: "center",
            backgroundRepeat: "no-repeat", backgroundColor: "#161616",
            pointerEvents: "none",
            transition: "background-image 0.8s ease-in-out"
        });
        document.body.appendChild(bgDiv);
        
        // Maak HA core transparant zodat de div zichtbaar is
        document.body.style.setProperty('background', 'transparent', 'important');
        document.body.style.setProperty('background-color', 'transparent', 'important');
    }
    
    bgDiv.style.backgroundImage = `url("${url}")`;
}

// Verbind direct met de officiële Home Assistant WebSocket API
if (window.hassConnection) {
    window.hassConnection.then(({ conn }) => {
        // 1. Haal de huidige status op zodra de app/pagina laadt
        conn.sendMessagePromise({ type: 'get_states' }).then((states) => {
            const bgState = states.find(s => s.entity_id === 'sensor.aion_logic_background_url');
            if (bgState) setAionBackground(bgState.state);
        });

        // 2. Luister live naar wijzigingen in het scenario
        conn.subscribeEvents((event) => {
            if (event.data.entity_id === 'sensor.aion_logic_background_url') {
                setAionBackground(event.data.new_state.state);
            }
        }, 'state_changed');
    });
}
