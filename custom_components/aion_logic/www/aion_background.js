/* Aion Logic™ - Autonome Dynamische Achtergrond Engine */
console.info("%c AION LOGIC: Dynamic Background Engine initialized ", "color: #78b7bb; font-weight: bold; background: #161616; padding: 4px; border-radius: 4px;");

function setAionBackground(url) {
    if (!url || url.includes("unavailable") || url.includes("unknown")) return;
    
    // Pas toe op de 'body' laag. Hierdoor schijnt hij door alle transparante HA-thema's heen, 
    // en overleeft hij elke Shadow DOM update van Home Assistant.
    document.body.style.setProperty('background-image', `url("${url}")`, 'important');
    document.body.style.setProperty('background-size', 'cover', 'important');
    document.body.style.setProperty('background-position', 'center', 'important');
    document.body.style.setProperty('background-repeat', 'no-repeat', 'important');
    document.body.style.setProperty('background-attachment', 'fixed', 'important');
    document.body.style.setProperty('background-color', '#161616', 'important');
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
