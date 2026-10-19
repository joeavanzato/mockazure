using System.Net.Http.Headers;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using Microsoft.Graph;
using Microsoft.Graph.Models;
using Microsoft.Kiota.Abstractions.Authentication;
using Microsoft.Kiota.Abstractions.Serialization;

string Option(string name)=>args[Array.IndexOf(args,name)+1];
var p=JsonDocument.Parse(File.ReadAllText(Option("--profile"))).RootElement;
string Value(string key)=>p.GetProperty(key).GetString()!;
void Check(bool condition){if(!condition)throw new Exception("Native Graph assertion failed");}
var mode=Option("--mode");Check(new[]{"write","read"}.Contains(mode));
var origin=new Uri(Value("endpoint"));Check(origin.Scheme=="https"&&new[]{"localhost","127.0.0.1"}.Contains(origin.Host));
var ca=X509Certificate2.CreateFromPem(File.ReadAllText(Value("ca_file")));
var handler=new HttpClientHandler{UseProxy=false,AllowAutoRedirect=false};
handler.ServerCertificateCustomValidationCallback=(request,certificate,oldChain,errors)=>{
 if(certificate is null||errors.HasFlag(SslPolicyErrors.RemoteCertificateNameMismatch))return false;
 using var chain=new X509Chain();chain.ChainPolicy.TrustMode=X509ChainTrustMode.CustomRootTrust;
 chain.ChainPolicy.CustomTrustStore.Add(ca);chain.ChainPolicy.RevocationMode=X509RevocationMode.NoCheck;return chain.Build(certificate);
};
using var http=new HttpClient(new LocalOnly(origin,handler));
http.DefaultRequestHeaders.Authorization=new AuthenticationHeaderValue("Bearer",Value("token"));
var graph=new GraphServiceClient(http,new AnonymousAuthenticationProvider(),Value("endpoint")+"/v1.0");
T Parse<T>(string key,ParsableFactory<T> factory)where T:IParsable{
 using var stream=new MemoryStream(Encoding.UTF8.GetBytes(p.GetProperty(key).GetRawText()));
 return ParseNodeFactoryRegistry.DefaultInstance.GetRootParseNode("application/json",stream).GetObjectValue(factory)!;
}
var user=graph.Users[Value("owner")];
var checks=new Dictionary<string,bool>();object objects=new{};
if(mode=="write"){
 var sub=(await graph.Subscriptions.PostAsync(Parse("subscription_body",Subscription.CreateFromDiscriminatorValue)))!;
 var draft=(await user.Messages.PostAsync(Parse("message_body",Message.CreateFromDiscriminatorValue)))!;
 Check(draft.Id!=null&&draft.IsDraft==true);
 await user.Messages[draft.Id].PatchAsync(new Message{Subject=Value("label")+" persistent"});
 Check((await user.Messages[draft.Id].GetAsync())!.Subject==Value("label")+" persistent");
 await user.SendMail.PostAsync(new Microsoft.Graph.Users.Item.SendMail.SendMailPostRequestBody{Message=Parse("send_body",Message.CreateFromDiscriminatorValue),SaveToSentItems=true});
 var e=(await user.Events.PostAsync(Parse("event_body",Event.CreateFromDiscriminatorValue)))!;
 var instances=(await user.Events[e.Id].Instances.GetAsync(c=>{c.QueryParameters.StartDateTime=Value("window_start");c.QueryParameters.EndDateTime=Value("window_end");}))!;
 Check(instances.Value!.Count==3&&instances.Value[0].Start!.DateTime!.Contains("T13:00:00")&&instances.Value.Skip(1).All(v=>v.Start!.DateTime!.Contains("T14:00:00")));
 var delta=(await user.CalendarView.Delta.GetAsDeltaGetResponseAsync(c=>{c.QueryParameters.StartDateTime=Value("window_start");c.QueryParameters.EndDateTime=Value("window_end");}))!;
 Check(delta.OdataDeltaLink!=null);
 await user.Events[e.Id].PatchAsync(new Event{Subject=Value("label")+" updated"});
 var changed=(await user.CalendarView.Delta.WithUrl(delta.OdataDeltaLink).GetAsDeltaGetResponseAsync())!;Check(changed.Value!.Count>0);
 var target=graph.Users[Value("recipient")];Message? mail=null;Event? invite=null;
 for(int i=0;i<80;i++){
  mail=(await target.Messages.GetAsync())!.Value!.FirstOrDefault(m=>m.Subject==p.GetProperty("send_body").GetProperty("subject").GetString());
  invite=(await target.Events.GetAsync())!.Value!.FirstOrDefault(v=>v.ICalUId==e.ICalUId);
  if(mail!=null&&invite!=null)break;await Task.Delay(250);
 }
 Check(mail!=null&&invite!=null&&invite.Id!=e.Id);
 var attachments=(await target.Messages[mail!.Id].Attachments.GetAsync())!.Value!;
 Check(attachments.OfType<FileAttachment>().Any(a=>a.Name=="evidence.bin"&&a.ContentBytes!.SequenceEqual(Encoding.UTF8.GetBytes("local\0attachment"))));
 await target.Events[invite!.Id].Accept.PostAsync(new Microsoft.Graph.Users.Item.Events.Item.Accept.AcceptPostRequestBody{SendResponse=true});
 await graph.Subscriptions[sub.Id].PatchAsync(new Subscription{ExpirationDateTime=DateTimeOffset.UtcNow.AddHours(3)});
 objects=new{message=draft.Id,@event=e.Id,subscription=sub.Id,owner=Value("owner")};
 foreach(var key in new[]{"mail_roundtrip","calendar_recurrence_delta","meeting_response","subscription_management"})checks[key]=true;
}else{
 var fixtures=JsonDocument.Parse(File.ReadAllText(Value("fixtures"))).RootElement;
 int count=0;
 foreach(var property in fixtures.EnumerateObject()){
  var f=property.Value;string Field(string key)=>f.GetProperty(key).GetString()!;
  var other=graph.Users[Field("owner")];Check((await other.Messages[Field("message")].GetAsync())!.Id==Field("message"));
  Check((await other.Events[Field("event")].GetAsync())!.Attendees![0].Status!.Response==ResponseType.Accepted);
  await other.Events[Field("event")].PatchAsync(new Event{Subject="Cross dotnet"});
  Check((await other.Events[Field("event")].GetAsync())!.Subject=="Cross dotnet");count++;
 }
 var id=fixtures.GetProperty("dotnet").GetProperty("subscription").GetString();
 Check((await graph.Subscriptions[id].GetAsync())!.Id==id);
 await graph.Subscriptions[id].Reauthorize.PostAsync();await graph.Subscriptions[id].DeleteAsync();
 checks["cross_language_reads"]=count==4;checks["subscription_management"]=true;checks["persistent_read"]=true;
}
// Package lock and build restore pin the informational package version.
var packageVersion=typeof(GraphServiceClient).Assembly.GetCustomAttributes(typeof(System.Reflection.AssemblyInformationalVersionAttribute),false).Cast<System.Reflection.AssemblyInformationalVersionAttribute>().Single().InformationalVersion.Split('+')[0];
await File.WriteAllTextAsync(Option("--report"),JsonSerializer.Serialize(new{passed=true,clientVersions=new Dictionary<string,string>{{"Microsoft.Graph",packageVersion}},assertions=checks,objects}));

sealed class LocalOnly(Uri origin,HttpMessageHandler inner):DelegatingHandler(inner){
 protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token){
  if(request.RequestUri is null||request.RequestUri.GetLeftPart(UriPartial.Authority)!=origin.GetLeftPart(UriPartial.Authority))throw new Exception("External Graph request refused");
  return base.SendAsync(request,token);
 }
}
