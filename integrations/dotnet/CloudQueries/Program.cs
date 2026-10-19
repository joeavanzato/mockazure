using System.Net.Security;
using System.ClientModel.Primitives;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using Azure;
using Azure.Core;
using Azure.Core.Pipeline;
using Azure.ResourceManager;
using Azure.ResourceManager.Resources;
using Azure.ResourceManager.Resources.Models;
using Azure.ResourceManager.ResourceGraph;
using Azure.ResourceManager.ResourceGraph.Models;
using Azure.ResourceManager.Monitor;
using Azure.ResourceManager.Monitor.Models;
using Azure.Monitor.Query.Logs;
using System.Reflection;

string Option(string name) {int i=Array.IndexOf(args,name);return i<0?throw new Exception("Missing argument"):args[i+1];}
var profile=JsonDocument.Parse(File.ReadAllText(Option("--profile"))).RootElement;
var endpoint=new Uri(profile.GetProperty("endpoint").GetString()!);
if(endpoint.Scheme!="https" || !new[]{"localhost","127.0.0.1"}.Contains(endpoint.Host))throw new Exception("Local HTTPS required");
var ca=X509Certificate2.CreateFromPem(File.ReadAllText(profile.GetProperty("ca_file").GetString()!));
var handler=new HttpClientHandler {UseProxy=false,AllowAutoRedirect=false};
handler.ServerCertificateCustomValidationCallback=(request,certificate,oldChain,errors)=>{
 if(certificate is null || errors.HasFlag(SslPolicyErrors.RemoteCertificateNameMismatch))return false;
 using var chain=new X509Chain();
 chain.ChainPolicy.TrustMode=X509ChainTrustMode.CustomRootTrust;
 chain.ChainPolicy.CustomTrustStore.Add(ca);chain.ChainPolicy.RevocationMode=X509RevocationMode.NoCheck;
 return chain.Build(certificate);
};
var logsEndpoint=new Uri(profile.GetProperty("logs_endpoint").GetString()!);
var local=new LocalOnly(new[]{endpoint,logsEndpoint},handler);
var options=new ArmClientOptions {Environment=new ArmEnvironment(endpoint,"https://management.azure.com/"),Transport=new HttpClientTransport(new HttpClient(local))};
options.Retry.MaxRetries=2;
options.SetApiVersion(TenantResource.ResourceType,"2021-01-01");

string Text(string name)=>profile.GetProperty(name).GetString()!;
string Token(string file)=>JsonDocument.Parse(File.ReadAllText(file)).RootElement.GetProperty("access_token").GetString()!;
var arm=new ArmClient(new FixedCredential(Token(Text("token_file"))),Text("subscription_id"),options);
var workflow=Option("--workflow");
var checks=new Dictionary<string,bool>();
var query=new ResourceQueryContent(Text("inventory_query")){Options=new ResourceQueryRequestOptions{Top=1,ResultFormat=ResultFormat.ObjectArray}};
query.Subscriptions.Add(Text("subscription_id"));
var tenant=arm.GetTenants().GetAll().Single(t=>t.Data.TenantId==Guid.Parse(Text("tenant_id")));
var seen=new List<string>();
do {
 var result=(await tenant.GetResourcesAsync(query)).Value;
 foreach(var row in JsonDocument.Parse(result.Data.ToString()).RootElement.EnumerateArray())seen.Add(row.GetProperty("id").GetString()!.ToLowerInvariant());
 query.Options.SkipToken=result.SkipToken;
}while(!string.IsNullOrEmpty(query.Options.SkipToken));
if(!seen.Order().SequenceEqual(profile.GetProperty("expected_resources").EnumerateArray().Select(x=>x.GetString()!).Order()))throw new Exception("Inventory paging differs");
checks["inventory_paging"]=true;
var settings=arm.GetDiagnosticSettings(new ResourceIdentifier(Text("source")));
if(workflow=="write"){
 var data=new DiagnosticSettingData{WorkspaceId=new ResourceIdentifier(Text("workspace_resource")),StorageAccountId=new ResourceIdentifier(Text("account")),LogAnalyticsDestinationType="Dedicated"};
 data.Logs.Add(new LogSettings(true){Category="StorageWrite"});
 await settings.CreateOrUpdateAsync(WaitUntil.Completed,Text("setting"),data);
}
var got=(await settings.GetAsync(Text("setting"))).Value.Data;
if(!got.WorkspaceId.ToString().Equals(Text("workspace_resource"),StringComparison.OrdinalIgnoreCase) || !got.Logs[0].IsEnabled)throw new Exception("Diagnostic setting differs");
checks["diagnostic_settings"]=true;
if(workflow=="read"){
 var logs=new LogsQueryClient(logsEndpoint,new FixedCredential(Token(Text("logs_token_file"))),new LogsQueryClientOptions{Transport=options.Transport});
 var result=(await logs.QueryWorkspaceAsync(Text("workspace_id"),Text("logs_query"),new LogsQueryTimeRange(TimeSpan.FromDays(30)))).Value;
 if(result.Table.Rows.Count!=1 || result.Table.Rows[0].GetInt64(0)!=profile.GetProperty("expected_logs").GetInt64())throw new Exception("Logs query differs");
 checks["native_logs_query"]=checks["cross_language_reads"]=true;
}
string Version(Type type)=>type.Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()!.InformationalVersion.Split('+')[0];
File.WriteAllText(Option("--report"),JsonSerializer.Serialize(new{ecosystem="dotnet",passed=true,assertions=checks,clientVersions=new Dictionary<string,string>{{"Azure.ResourceManager.ResourceGraph",Version(typeof(ResourceQueryContent))},{"Azure.ResourceManager.Monitor",Version(typeof(DiagnosticSettingData))},{"Azure.Monitor.Query.Logs",Version(typeof(LogsQueryClient))}},transport="sdk",workflow}));
sealed class FixedCredential(string token):TokenCredential {
 public override AccessToken GetToken(TokenRequestContext context,CancellationToken cancellationToken)=>new(token,DateTimeOffset.UtcNow.AddMinutes(30));
 public override ValueTask<AccessToken> GetTokenAsync(TokenRequestContext context,CancellationToken cancellationToken)=>ValueTask.FromResult(GetToken(context,cancellationToken));
}
sealed class LocalOnly(Uri[] endpoints,HttpMessageHandler inner):DelegatingHandler(inner){
 protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct){
  if(request.RequestUri is null || !endpoints.Any(endpoint=>request.RequestUri.GetLeftPart(UriPartial.Authority)==endpoint.GetLeftPart(UriPartial.Authority)))throw new Exception("Client request escaped local origin");
  return base.SendAsync(request,ct);
 }
}
