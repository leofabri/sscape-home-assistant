import {element} from "./setup-preview.js";
import {action} from "./setup-controller.js";

export function renderTests(panel, main) {
  const details=element("details");details.append(element("summary",panel.text("Prova notifiche e Assist","Test notifications and Assist")));
  if(panel.testMessage){details.open=true;details.append(element("p",panel.testMessage,{role:"status"}));}
  const choices=panel.destinations || [];
  const select=element("select",null,{disabled:panel.busy,ariaLabel:panel.text("Dispositivo o destinazione","Device or destination")});
  for(const row of choices)select.append(element("option",row.label,{value:row.value}));
  const result=async(service,data)=>{
    if(panel.busy)return;panel.busy=true;
    try {
      const value=await action(panel._hass,service,data);
      panel.testMessage=service==="test_notification"?panel.text("Richiesta accettata. Controlla il telefono: la ricezione non è verificata automaticamente.","Request accepted. Check your device; receipt is not verified automatically."):
        value.available?value.phrase+" → "+value.speech:panel.text("Abilita Assist ed esponi il luogo nelle opzioni.","Enable Assist and expose the target in options.");
    } catch {panel.testMessage=panel.text("Prova non riuscita. Controlla la configurazione o attendi prima di riprovare.","Test failed. Check configuration or wait before retrying.");}
    panel.busy=false;panel.render();
  };
  if(choices.length)details.append(select,panel.button(panel.text("Invia notifica di prova","Send test notification"),()=>result("test_notification",{notification_target:select.value})));
  else details.append(element("p",panel.text("Nessuna destinazione notifiche disponibile. Configura prima la Companion App o un servizio di notifica.","No notification destination available. Configure the Companion App or a notification service first.")));
  if(panel.selected)details.append(panel.button(panel.text("Prova una frase Assist per questo luogo","Test an Assist phrase for this target"),()=>result("test_assist",{device_id:panel.selected})));
  details.append(element("p",panel.text("Assist: questo test controlla la risposta del motore integrato, non microfono o altoparlante.","Assist: this checks the built-in engine response, not microphone or speaker delivery.")));
  main.append(details);
}
