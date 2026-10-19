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
using Azure.ResourceManager.Network;
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
var local=new LocalOnly(endpoint,handler);
var options=new ArmClientOptions {Environment=new ArmEnvironment(endpoint,"https://management.azure.com/"),Transport=new HttpClientTransport(new HttpClient(local))};
options.Retry.MaxRetries=2;
var resources=profile.GetProperty("resources").EnumerateArray().ToArray();
var reads=profile.GetProperty("read_resources").EnumerateArray().ToArray();
foreach(var row in resources.Concat(reads))options.SetApiVersion(new ResourceType(row.GetProperty("type").GetString()!),row.GetProperty("version").GetString()!);
var token=JsonDocument.Parse(File.ReadAllText(profile.GetProperty("token_file").GetString()!)).RootElement.GetProperty("access_token").GetString()!;
var client=new ArmClient(new FixedCredential(token),profile.GetProperty("subscription_id").GetString(),options);
var started=DateTimeOffset.UtcNow;
var workflow=Option("--workflow");
object assertions;
if(workflow=="cleanup") {
 foreach(var row in resources.Reverse())await client.GetGenericResource(new ResourceIdentifier(row.GetProperty("id").GetString()!)).DeleteAsync(WaitUntil.Completed);
 assertions=new{deleted=resources.Length};
}else{
 foreach(var row in resources){
  Console.Error.WriteLine(row.GetProperty("type").GetString());
  var body=row.GetProperty("body");
  var data=ModelReaderWriter.Read<GenericResourceData>(BinaryData.FromString(body.GetRawText()))!;
  var id=new ResourceIdentifier(row.GetProperty("id").GetString()!);
  ResourceIdentifier created;
  if(id.ResourceType.ToString()=="Microsoft.Network/networkSecurityGroups/securityRules") {
   var typed=ModelReaderWriter.Read<SecurityRuleData>(BinaryData.FromString(body.GetRawText()))!;
   created=(await client.GetNetworkSecurityGroupResource(id.Parent).GetSecurityRules().CreateOrUpdateAsync(WaitUntil.Completed,id.Name,typed)).Value.Id;
  } else if(id.ResourceType.ToString()=="Microsoft.Network/virtualNetworks/subnets") {
   var typed=ModelReaderWriter.Read<SubnetData>(BinaryData.FromString(body.GetRawText()))!;
   created=(await client.GetVirtualNetworkResource(id.Parent).GetSubnets().CreateOrUpdateAsync(WaitUntil.Completed,id.Name,typed)).Value.Id;
  } else created=(await client.GetGenericResources().CreateOrUpdateAsync(WaitUntil.Completed,id,data)).Value.Id;
  if(created!=id)throw new Exception("Create identity differs");
  if((await client.GetGenericResource(id).GetAsync()).Value.Id!=id)throw new Exception("Read identity differs");
 }
 foreach(var row in reads){var id=new ResourceIdentifier(row.GetProperty("id").GetString()!);if((await client.GetGenericResource(id).GetAsync()).Value.Id!=id)throw new Exception("Cross-language identity differs");}
 var target=resources.First(row=>row.GetProperty("type").GetString()=="Microsoft.Compute/virtualMachines");
 var resource=client.GetGenericResource(new ResourceIdentifier(target.GetProperty("id").GetString()!));
 var update=ModelReaderWriter.Read<GenericResourceData>(BinaryData.FromString(target.GetProperty("body").GetRawText()))!;update.Tags.Add("writer","dotnet");
 await client.GetGenericResources().CreateOrUpdateAsync(WaitUntil.Completed,resource.Id,update);
 if((await resource.GetAsync()).Value.Data.Tags["writer"]!="dotnet")throw new Exception("Update differs");
 assertions=new{created=resources.Length,cross_reads=reads.Length,updated=true};
}
File.WriteAllText(Option("--report"),JsonSerializer.Serialize(new{ecosystem="dotnet",clientVersions=new Dictionary<string,string>{{"Azure.ResourceManager",typeof(ArmClient).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()!.InformationalVersion.Split('+')[0]},{"Azure.ResourceManager.Network",typeof(SubnetData).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()!.InformationalVersion.Split('+')[0]}},transport="sdk",workflow,assertions,passed=true,elapsedSeconds=(DateTimeOffset.UtcNow-started).TotalSeconds}));

sealed class FixedCredential(string token):TokenCredential {
 public override AccessToken GetToken(TokenRequestContext context,CancellationToken cancellationToken)=>new(token,DateTimeOffset.UtcNow.AddMinutes(30));
 public override ValueTask<AccessToken> GetTokenAsync(TokenRequestContext context,CancellationToken cancellationToken)=>ValueTask.FromResult(GetToken(context,cancellationToken));
}
sealed class LocalOnly(Uri endpoint,HttpMessageHandler inner):DelegatingHandler(inner){
 protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken ct){
  if(request.RequestUri is null || request.RequestUri.GetLeftPart(UriPartial.Authority)!=endpoint.GetLeftPart(UriPartial.Authority))throw new Exception("Client request escaped local origin");
  return base.SendAsync(request,ct);
 }
}
