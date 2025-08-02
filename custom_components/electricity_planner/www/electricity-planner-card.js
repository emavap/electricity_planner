class ElectricityPlannerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  setConfig(config) {
    this.config = config;
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  render() {
    if (!this._hass) return;

    const batteryCharging = this._hass.states['binary_sensor.electricity_planner_battery_grid_charging'];
    const carCharging = this._hass.states['binary_sensor.electricity_planner_car_grid_charging'];
    const priceAnalysis = this._hass.states['sensor.electricity_planner_price_analysis'];
    const batteryAnalysis = this._hass.states['sensor.electricity_planner_battery_analysis'];
    const powerAnalysis = this._hass.states['sensor.electricity_planner_power_analysis'];
    const lowPrice = this._hass.states['binary_sensor.electricity_planner_low_electricity_price'];

    const batteryOn = batteryCharging?.state === 'on';
    const carOn = carCharging?.state === 'on';
    const currentPrice = priceAnalysis?.state ? parseFloat(priceAnalysis.state) : 0;
    const priceAttrs = priceAnalysis?.attributes || {};
    const batteryAttrs = batteryAnalysis?.attributes || {};
    const powerAttrs = powerAnalysis?.attributes || {};

    let chargingStatus = '⏳ Wait - No Grid Charging';
    let chargingIcon = 'mdi:flash-off';
    let chargingColor = '#f44336';

    if (batteryOn && carOn) {
      chargingStatus = '🔋⚡ Charge Both from Grid';
      chargingIcon = 'mdi:flash';
      chargingColor = '#4caf50';
    } else if (batteryOn) {
      chargingStatus = '🔋 Charge Battery from Grid';
      chargingIcon = 'mdi:battery-charging';
      chargingColor = '#4caf50';
    } else if (carOn) {
      chargingStatus = '⚡ Charge Car from Grid';
      chargingIcon = 'mdi:car-electric';
      chargingColor = '#4caf50';
    }

    const priceColor = currentPrice < 0.10 ? '#4caf50' : currentPrice < 0.20 ? '#ff9800' : '#f44336';
    const batterySOC = batteryAnalysis?.state ? parseFloat(batteryAnalysis.state) : 0;
    const batteryColor = batterySOC >= 80 ? '#4caf50' : batterySOC >= 40 ? '#ff9800' : '#f44336';

    this.shadowRoot.innerHTML = `
      <style>
        .card {
          background: var(--ha-card-background, var(--card-background-color, white));
          border-radius: var(--ha-card-border-radius, 12px);
          border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color, #e0e0e0));
          box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,0.1));
          padding: 16px;
          margin: 8px;
          font-family: var(--paper-font-body1_-_font-family);
        }
        .header {
          display: flex;
          align-items: center;
          margin-bottom: 16px;
        }
        .header-icon {
          width: 40px;
          height: 40px;
          margin-right: 12px;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          color: white;
        }
        .header-text {
          flex: 1;
        }
        .title {
          font-size: 1.2em;
          font-weight: 500;
          margin: 0;
        }
        .subtitle {
          font-size: 0.9em;
          color: var(--secondary-text-color);
          margin: 0;
        }
        .badge {
          background: ${priceColor};
          color: white;
          padding: 4px 8px;
          border-radius: 12px;
          font-size: 0.8em;
          font-weight: 500;
        }
        .content {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 16px;
        }
        .section {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .metric {
          display: flex;
          align-items: center;
          padding: 8px;
          border-radius: 8px;
          background: var(--secondary-background-color, #f5f5f5);
        }
        .metric-icon {
          width: 24px;
          height: 24px;
          margin-right: 8px;
          color: ${chargingColor};
        }
        .metric-text {
          flex: 1;
        }
        .metric-label {
          font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .metric-value {
          font-size: 1em;
          font-weight: 500;
        }
        .price-bar {
          height: 8px;
          background: linear-gradient(to right, #4caf50 0%, #4caf50 30%, #ff9800 30%, #ff9800 70%, #f44336 70%, #f44336 100%);
          border-radius: 4px;
          margin: 8px 0;
          position: relative;
        }
        .price-indicator {
          position: absolute;
          top: -2px;
          width: 2px;
          height: 12px;
          background: #333;
          border-radius: 1px;
          left: ${(priceAttrs.price_position || 0) * 100}%;
        }
        .decisions {
          grid-column: 1 / -1;
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 8px;
          margin-top: 8px;
        }
        .decision {
          padding: 12px;
          border-radius: 8px;
          text-align: center;
          color: white;
          font-weight: 500;
        }
        .decision.on {
          background: #4caf50;
        }
        .decision.off {
          background: #f44336;
        }
        .decision-reason {
          font-size: 0.8em;
          opacity: 0.9;
          margin-top: 4px;
        }
      </style>
      
      <div class="card">
        <div class="header">
          <div class="header-icon" style="background: ${chargingColor}">
            <ha-icon icon="${chargingIcon}"></ha-icon>
          </div>
          <div class="header-text">
            <div class="title">Electricity Planner</div>
            <div class="subtitle">${chargingStatus}</div>
          </div>
          <div class="badge">${currentPrice.toFixed(3)}€/kWh</div>
        </div>
        
        <div class="content">
          <div class="section">
            <div class="metric">
              <ha-icon class="metric-icon" icon="mdi:currency-eur"></ha-icon>
              <div class="metric-text">
                <div class="metric-label">Current Price</div>
                <div class="metric-value">${currentPrice.toFixed(3)} €/kWh</div>
              </div>
            </div>
            
            <div class="metric">
              <ha-icon class="metric-icon" icon="mdi:battery" style="color: ${batteryColor}"></ha-icon>
              <div class="metric-text">
                <div class="metric-label">Battery SOC (${batteryAttrs.batteries_count || 0} batteries)</div>
                <div class="metric-value">${batterySOC.toFixed(0)}%</div>
              </div>
            </div>
          </div>
          
          <div class="section">
            <div class="metric">
              <ha-icon class="metric-icon" icon="mdi:home-lightning-bolt" style="color: #607d8b"></ha-icon>
              <div class="metric-text">
                <div class="metric-label">House Power</div>
                <div class="metric-value">${powerAnalysis?.state || 0}W</div>
              </div>
            </div>
            
            <div class="metric">
              <ha-icon class="metric-icon" icon="mdi:solar-power" style="color: #ffc107"></ha-icon>
              <div class="metric-text">
                <div class="metric-label">Solar Surplus</div>
                <div class="metric-value">${powerAttrs.solar_surplus || 0}W</div>
              </div>
            </div>
          </div>
          
          <div style="grid-column: 1 / -1;">
            <div class="metric-label">Price Position in Daily Range</div>
            <div class="price-bar">
              <div class="price-indicator"></div>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 0.8em; color: var(--secondary-text-color);">
              <span>Low: ${(priceAttrs.lowest_price || 0).toFixed(3)}€</span>
              <span>${((priceAttrs.price_position || 0) * 100).toFixed(0)}%</span>
              <span>High: ${(priceAttrs.highest_price || 0).toFixed(3)}€</span>
            </div>
          </div>
          
          <div class="decisions">
            <div class="decision ${batteryOn ? 'on' : 'off'}">
              <div>🔋 Battery Grid</div>
              <div class="decision-reason">${batteryCharging?.attributes?.reason || 'No data'}</div>
            </div>
            <div class="decision ${carOn ? 'on' : 'off'}">
              <div>⚡ Car Grid</div>
              <div class="decision-reason">${carCharging?.attributes?.reason || 'No data'}</div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  getCardSize() {
    return 4;
  }

  static get styles() {
    return [];
  }
}

customElements.define('electricity-planner-card', ElectricityPlannerCard);

// Register the card
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'electricity-planner-card',
  name: 'Electricity Planner Card',
  description: 'A comprehensive card showing electricity planning decisions',
  preview: true,
  documentationURL: 'https://github.com/emavap/electricity_planner',
});

console.info(
  `%c ELECTRICITY-PLANNER-CARD %c v1.0.0 `,
  'color: orange; font-weight: bold; background: black',
  'color: white; font-weight: bold; background: dimgray',
);