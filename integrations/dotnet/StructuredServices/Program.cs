using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using System.Reflection;
using Azure;
using Azure.Core;
using Azure.Core.Pipeline;
using Azure.Data.Tables;
using Azure.Storage;
using Azure.Storage.Queues;
using Azure.Security.KeyVault.Secrets;

string Option(string name)=>args[Array.IndexOf(args,name)+1];
var p=JsonDocument.Parse(File.ReadAllText(Option("--profile"))).RootElement;
string Value(string key)=>p.GetProperty(key).GetString()!;
var mode=Option("--mode");
if(!new[]{"write","read","restore"}.Contains(mode))throw new Exception("Unknown workflow mode");
void Check(bool condition){if(!condition)throw new Exception("Native structured assertion failed");}
var origins=new[]{new Uri(Value("table_endpoint").TrimEnd('/')),new Uri(Value("queue_endpoint")),new Uri(Value("vault_endpoint").TrimEnd('/'))};
foreach(var origin in origins)Check(origin.Scheme=="https"&&new[]{"localhost","127.0.0.1"}.Contains(origin.Host));
var ca=X509Certificate2.CreateFromPem(File.ReadAllText(Value("ca_file")));
var handler=new HttpClientHandler{UseProxy=false,AllowAutoRedirect=false};
handler.ServerCertificateCustomValidationCallback=(request,certificate,oldChain,errors)=>{
 if(certificate is null||errors.HasFlag(SslPolicyErrors.RemoteCertificateNameMismatch))return false;
 using var chain=new X509Chain();chain.ChainPolicy.TrustMode=X509ChainTrustMode.CustomRootTrust;
 chain.ChainPolicy.CustomTrustStore.Add(ca);chain.ChainPolicy.RevocationMode=X509RevocationMode.NoCheck;return chain.Build(certificate);
};
var transport=new HttpClientTransport(new HttpClient(new LocalOnly(origins,handler)));
var tableOptions=new TableClientOptions(TableClientOptions.ServiceVersion.V2019_02_02){Transport=transport};tableOptions.Retry.MaxRetries=0;
var table=new TableClient(origins[0],"DotnetPersist",new TableSharedKeyCredential(Value("account"),Value("key")),tableOptions);
var queueOptions=new QueueClientOptions(QueueClientOptions.ServiceVersion.V2026_02_06){Transport=transport,MessageEncoding=QueueMessageEncoding.None};queueOptions.Retry.MaxRetries=0;
var queues=new QueueServiceClient(origins[1],new StorageSharedKeyCredential(Value("account"),Value("key")),queueOptions);
var queue=queues.GetQueueClient("dotnet-persist");
var secretOptions=new SecretClientOptions(SecretClientOptions.ServiceVersion.V2025_07_01){Transport=transport,DisableChallengeResourceVerification=true};secretOptions.Retry.MaxRetries=0;
var credential=new LocalCredential(Value("token"));
var secrets=new SecretClient(origins[2],credential,secretOptions);
var secretName="dotnet-secret";
var assertions=new Dictionary<string,bool>();
async Task Denied(Func<Task> action,int status){try{await action();throw new Exception("Expected failure was accepted");}catch(RequestFailedException error){Check(error.Status==status);}}
if(mode=="write"){
 await table.CreateAsync();
 var created=await table.AddEntityAsync(new TableEntity("p","one"){{"Count",9007199254740993L},{"Bytes",new byte[]{0,1,255}}});
 var first=(await table.GetEntityAsync<TableEntity>("p","one")).Value;
 var firstEtag=created.Headers.ETag!.Value;
 await table.UpdateEntityAsync(new TableEntity("p","one"){{"Keep",true}},firstEtag,TableUpdateMode.Merge);
 await Denied(async()=>{await table.UpdateEntityAsync(new TableEntity("p","one"){{"Keep",false}},firstEtag,TableUpdateMode.Merge);},412);
 await table.SubmitTransactionAsync(new[]{new TableTransactionAction(TableTransactionActionType.Add,new TableEntity("p","two")),new TableTransactionAction(TableTransactionActionType.Add,new TableEntity("p","three"))});
 await Denied(async()=>{await table.SubmitTransactionAsync(new[]{new TableTransactionAction(TableTransactionActionType.Add,new TableEntity("p","rollback")),new TableTransactionAction(TableTransactionActionType.Add,new TableEntity("p","one"))});},409);
 var count=0;await foreach(var item in table.QueryAsync<TableEntity>("PartitionKey eq 'p'",maxPerPage:1))count++;
 Check(count==3);await table.DeleteEntityAsync("p","three");assertions["tableMutation"]=true;
 await queue.CreateAsync();await queue.SetMetadataAsync(new Dictionary<string,string>{{"writer","dotnet"}});
 await queue.SendMessageAsync("first",timeToLive:TimeSpan.FromSeconds(-1));
 var message=(await queue.ReceiveMessageAsync(TimeSpan.FromSeconds(30))).Value;
 var changed=(await queue.UpdateMessageAsync(message.MessageId,message.PopReceipt,"second",TimeSpan.Zero)).Value;
 Check(changed.PopReceipt!=message.PopReceipt);
 await Denied(async()=>{await queue.DeleteMessageAsync(message.MessageId,message.PopReceipt);},400);
 var next=(await queue.ReceiveMessageAsync()).Value;Check(next.MessageText=="second"&&next.DequeueCount==2);await queue.DeleteMessageAsync(next.MessageId,next.PopReceipt);
 await queue.SendMessageAsync("persistent ☃",timeToLive:TimeSpan.FromSeconds(-1));assertions["queueReceipts"]=true;
 var a=(await secrets.SetSecretAsync(secretName,"first")).Value;var b=(await secrets.SetSecretAsync(secretName,"second")).Value;
 Check(a.Properties.Version!=b.Properties.Version);Check((await secrets.GetSecretAsync(secretName,a.Properties.Version)).Value.Value=="first");
 File.WriteAllBytes(Option("--backup"),(await secrets.BackupSecretAsync(secretName)).Value);
 await (await secrets.StartDeleteSecretAsync(secretName)).WaitForCompletionAsync();
 Check((await secrets.GetDeletedSecretAsync(secretName)).Value.Name==secretName);
 await (await secrets.StartRecoverDeletedSecretAsync(secretName)).WaitForCompletionAsync();
 Check((await secrets.GetSecretAsync(secretName)).Value.Properties.Version==b.Properties.Version);assertions["secretRecovery"]=true;
}
if(mode=="restore"||mode=="write"){
 await (await secrets.StartDeleteSecretAsync(secretName)).WaitForCompletionAsync();await secrets.PurgeDeletedSecretAsync(secretName);
 await secrets.RestoreSecretBackupAsync(File.ReadAllBytes(Option("--backup")));assertions["secretBackupRestore"]=true;
}
var entity=(await table.GetEntityAsync<TableEntity>("p","one")).Value;
Check(entity.GetInt64("Count")==9007199254740993L&&entity.GetBoolean("Keep")==true&&entity.GetBinary("Bytes")!.SequenceEqual(new byte[]{0,1,255}));
Check((await queue.PeekMessageAsync()).Value.MessageText=="persistent ☃");
Check((await secrets.GetSecretAsync(secretName)).Value.Value=="second");
var versions=0;await foreach(var item in secrets.GetPropertiesOfSecretVersionsAsync(secretName))versions++;
Check(versions==2&&credential.Calls>0);assertions["persistentRead"]=true;
string Version(Type type)=>type.Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()!.InformationalVersion.Split('+')[0];
File.WriteAllText(Option("--report"),JsonSerializer.Serialize(new{passed=true,mode,assertions,clientVersions=new Dictionary<string,string>{{"Azure.Data.Tables",Version(typeof(TableClient))},{"Azure.Storage.Queues",Version(typeof(QueueClient))},{"Azure.Security.KeyVault.Secrets",Version(typeof(SecretClient))}}}));

class LocalOnly(Uri[] origins,HttpMessageHandler handler):DelegatingHandler(handler){
 protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token){if(!origins.Any(u=>request.RequestUri!.GetLeftPart(UriPartial.Authority)==u.GetLeftPart(UriPartial.Authority)))throw new Exception("Nonlocal request rejected");var response=await base.SendAsync(request,token);if(!response.IsSuccessStatusCode)Console.Error.WriteLine($"{request.Method} {request.RequestUri!.AbsolutePath}: {(int)response.StatusCode}");return response;}
}
class LocalCredential(string token):TokenCredential{
 public int Calls=0;
 public override AccessToken GetToken(TokenRequestContext context,CancellationToken cancellationToken){if(!context.Scopes.SequenceEqual(new[]{"https://vault.azure.net/.default"}))throw new Exception("Unexpected token scope");Calls++;return new AccessToken(token,DateTimeOffset.UtcNow.AddHours(1));}
 public override ValueTask<AccessToken> GetTokenAsync(TokenRequestContext context,CancellationToken cancellationToken)=>new(GetToken(context,cancellationToken));
}
