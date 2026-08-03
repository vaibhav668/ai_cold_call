import asyncio
import os
import uuid
from sqlalchemy import select
from app.db.session import get_session_maker
from app.models.campaign import Campaign
from app.models.customer import Customer
from app.models.campaign_lead import CampaignLead
from app.models.document import Document
from app.services.workflow_service import WorkflowService
from app.services.rag_service import RAGService

# Create folders if not exist
DOCS_DIR = os.path.join("data", "documents", "premium_residential")
os.makedirs(DOCS_DIR, exist_ok=True)

# 1. Operational RAG documents data for the Premium Residential campaign
PREMIUM_RESIDENTIAL_DOCS = {
    "company_introduction.txt": (
        "Company Introduction:\n"
        "VoiceAgent.AI Realty (calling on behalf of Crown Estates) is one of India's leading premium developers.\n"
        "With over two decades of experience, we have delivered 50+ landmark residential and commercial projects.\n"
        "We are known for our design excellence, robust engineering, and timely delivery."
    ),
    "available_projects.txt": (
        "Available Projects:\n"
        "Our flagship project in Bengaluru is 'Crown Heights' located in Whitefield.\n"
        "We also offer 'Crown Meadows' in Sarjapur and 'Crown Breeze' in Electronic City.\n"
        "All projects are RERA approved and feature ultra-premium luxury living."
    ),
    "apartment_types.txt": (
        "Apartment Types:\n"
        "Crown Heights features luxury 2 BHK and 3 BHK apartments.\n"
        "- 2 BHK Super Built-up Area: 1,200 sq.ft to 1,450 sq.ft.\n"
        "- 3 BHK Super Built-up Area: 1,650 sq.ft to 2,100 sq.ft.\n"
        "All apartments are vastu-compliant and boast three-sided open views."
    ),
    "pricing.txt": (
        "Pricing Guide:\n"
        "- Luxury 2 BHK apartments: Starting from ₹85 Lakhs up to ₹1.1 Crore.\n"
        "- Ultra-premium 3 BHK apartments: Starting from ₹1.25 Crore up to ₹1.7 Crore.\n"
        "These prices are inclusive of car parking, clubhouse membership, and amenities share."
    ),
    "amenities.txt": (
        "Amenities List:\n"
        "We offer 40+ world-class amenities to our residents:\n"
        "- Clubhouse: A grand 50,000 sq.ft clubhouse with lounge, dining, and squash courts.\n"
        "- Swimming Pool: Olympic-sized temperature-controlled infinity pool and kids splash pool.\n"
        "- Gym: Fully equipped gymnasium with cardio, weight training, and personal trainers.\n"
        "- Children's Play Area: Dedicated safe outdoor playground and indoor arcade/creche.\n"
        "- Security: 24/7 multi-tier smart security with app-controlled visitors access and CCTV.\n"
        "- Parking: Secure covered multi-level car parking space for residents and dedicated visitors slots."
    ),
    "loan_assistance.txt": (
        "Home Loan Assistance:\n"
        "We offer comprehensive doorstep loan assistance services.\n"
        "We have direct tie-ups with SBI, HDFC Bank, ICICI Bank, Axis Bank, and LIC Housing Finance.\n"
        "Pre-approved home loans can be processed with attractive interest rates starting from 8.4%."
    ),
    "booking_process.txt": (
        "Booking Process:\n"
        "1. Select your preferred apartment layout and block.\n"
        "2. Submit the booking application form with KYC documents (PAN, Aadhaar).\n"
        "3. Pay the booking token amount of ₹2 Lakhs (payable via credit card, UPI, net banking, or cheque).\n"
        "4. Allotment letter is issued within 3 working days."
    ),
    "site_visit_process.txt": (
        "Site Visit Process:\n"
        "We arrange free personalized site visits for our registered customers daily from 9:00 AM to 6:00 PM.\n"
        "This includes complimentary AC chauffeur pick-up and drop from your residence or office.\n"
        "A dedicated relationship manager will accompany you to show the model flat and explain project layouts."
    ),
    "cancellation_refund_policy.txt": (
        "Cancellation & Refund Policy:\n"
        "If you cancel the booking within 15 days of token payment, you will receive a 100% full refund.\n"
        "Cancellations after 15 days but before the sale agreement incur a processing charge of ₹25,000.\n"
        "Cancellations after signing the sale agreement are subject to terms outlined in the contract (up to 10% forfeiture)."
    ),
    "payment_plans.txt": (
        "Payment Plans:\n"
        "We offer flexible payment plans to suit your requirements:\n"
        "1. Construction-Linked Plan (CLP): Pay in installments tied to construction milestones.\n"
        "2. 10:90 Subvention Plan: Pay 10% now, and no EMI until possession.\n"
        "3. Special Down Payment Discount: Avail up to 5% discount on full upfront payment."
    ),
    "construction_timeline.txt": (
        "Construction & Possession Timeline:\n"
        "Construction is currently in full swing at Whitefield Crown Heights.\n"
        "- Towers A and B: Structural work complete, possession by December 2026.\n"
        "- Towers C and D: Excavation complete, structural work in progress, possession by June 2027.\n"
        "RERA Registration number: PRM/KA/RERA/1251/446/PR/2026."
    ),
    "frequently_asked_questions.txt": (
        "Frequently Asked Questions (FAQ):\n"
        "Q: Are there any hidden charges?\n"
        "A: No, our pricing is fully transparent. Stamp duty, registration fees, and GST are extra as per actuals.\n"
        "Q: Is the land freehold?\n"
        "A: Yes, the land is 100% clear-title, freehold, and legally verified."
    )
}

async def seed_documents(session, campaign_id, docs_dict):
    """Write text files and index them into ChromaDB knowledge base."""
    rag_service = RAGService()
    await rag_service.initialize_collection()
    
    for filename, content in docs_dict.items():
        # 1. Write file
        file_path = os.path.join(DOCS_DIR, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        # 2. Insert record in PostgreSQL
        document = Document(
            id=uuid.uuid4(),
            campaign_id=campaign_id,
            filename=filename,
            file_type="txt",
            status="active",
            total_chunks=0
        )
        session.add(document)
        await session.flush()
        
        # 3. Index in ChromaDB
        chunks_indexed = await rag_service.index_document(
            campaign_id=campaign_id,
            document_id=document.id,
            filename=filename,
            text=content
        )
        
        # Update chunk counts
        document.total_chunks = chunks_indexed
        print(f"Indexed doc '{filename}' into ChromaDB for campaign {campaign_id} ({chunks_indexed} chunks).")

async def main():
    print("Initializing Seeding for Premium Residential Test Case...")
    async_session_maker = get_session_maker()
    async with async_session_maker() as session:
        # Step 1: Create Campaign
        campaign_name = "Premium Residential Lead Qualification"
        camp_query = select(Campaign).where(Campaign.name == campaign_name)
        camp_res = await session.execute(camp_query)
        campaign = camp_res.scalars().first()
        
        if not campaign:
            campaign = Campaign(
                id=uuid.uuid4(),
                name=campaign_name,
                description="Outbound AI Cold Calling campaign to qualify premium residential leads.",
                workflow_type="real_estate",
                status="active",
                max_retries=3,
                retry_interval_minutes=60,
                is_active=True
            )
            session.add(campaign)
            await session.flush()
            
            # Seed default real estate template
            workflow_service = WorkflowService(session)
            await workflow_service.seed_campaign_defaults(campaign.id, "real_estate")
            
            # Customize PromptTemplate goals to include Whitefield Crown Heights specific parameters
            from app.models.prompt_template import PromptTemplate
            tmpl_query = select(PromptTemplate).where(PromptTemplate.campaign_id == campaign.id)
            tmpl_res = await session.execute(tmpl_query)
            template = tmpl_res.scalars().first()
            if template:
                template.system_prompt = (
                    "You are VoiceAgent.AI, a highly professional and polite real estate sales representative "
                    "calling on behalf of Crown Estates regarding our premium residential properties. "
                    "Introduce yourself naturally, keep the tone warm, friendly, and engaging. "
                    "Always speak clearly, use short conversational sentences, and guide the customer naturally "
                    "without sounding scripted."
                )
                template.conversation_goals = (
                    "- Goal 1: Introduce yourself as VoiceAgent.AI calling on behalf of our premium residential project.\n"
                    "- Goal 2: Greet the lead {{first_name}} and ask if this is a good time to speak.\n"
                    "- Goal 3: Understand their requirements (preferred location, property type, budget, purchase timeline).\n"
                    "- Goal 4: If they ask about amenities, booking process, starting price, or timelines, use the 'lookup_knowledge' tool to fetch facts.\n"
                    "- Goal 5: Handle objections politely and answer questions directly.\n"
                    "- Goal 6: If they show interest or say 'I am interested', offer to book a site visit tour (which includes a chauffeur pick-up) by calling the 'book_appointment' tool.\n"
                    "- Goal 7: End the call professionally."
                )
                session.add(template)
            
            print(f"Created Campaign: {campaign.name} (ID: {campaign.id})")
        else:
            print(f"Campaign already exists: {campaign.name} (ID: {campaign.id})")

        # Step 2: Create Customer
        customer_phone = "+918266894170"
        cust_query = select(Customer).where(Customer.phone_number == customer_phone)
        cust_res = await session.execute(cust_query)
        customer = cust_res.scalars().first()
        
        custom_vars = {
            "preferred_language": "English",
            "country": "India",
            "lead_source": "Website Registration",
            "property_interest": "Apartments",
            "city_preference": "Bengaluru",
            "budget": "₹80 Lakhs – ₹1.2 Crore",
            "purchase_purpose": "Self Use",
            "property_type": "2 BHK",
            "lead_status": "New"
        }
        
        if not customer:
            customer = Customer(
                id=uuid.uuid4(),
                first_name="Vaibhav",
                last_name="Pokhriyal",
                phone_number=customer_phone,
                email="vpokhriyal35@gmail.com",
                custom_variables=custom_vars,
                is_active=True
            )
            session.add(customer)
            await session.flush()
            print(f"Created Customer: {customer.first_name} {customer.last_name} (ID: {customer.id})")
        else:
            customer.custom_variables = custom_vars
            session.add(customer)
            print(f"Customer already exists: {customer.first_name} {customer.last_name} (ID: {customer.id})")

        # Step 3: Link Customer to Campaign
        lead_query = select(CampaignLead).where(
            CampaignLead.campaign_id == campaign.id,
            CampaignLead.customer_id == customer.id
        )
        lead_res = await session.execute(lead_query)
        lead = lead_res.scalars().first()
        
        if not lead:
            lead = CampaignLead(
                campaign_id=campaign.id,
                customer_id=customer.id,
                status="pending"
            )
            session.add(lead)
            print(f"Linked lead: {customer.first_name} -> {campaign.name}")
        else:
            print(f"Lead link already exists.")
            
        await session.commit()
        
        # Step 4: Write and index Premium Real Estate RAG documents
        print("Writing and indexing RAG knowledge documents for Premium Real Estate...")
        await seed_documents(session, campaign.id, PREMIUM_RESIDENTIAL_DOCS)
        await session.commit()
        
        print("\n==============================================")
        print("SEEDING COMPLETED SUCCESSFULLY!")
        print(f"Campaign ID: {campaign.id}")
        print(f"Customer ID: {customer.id}")
        print("==============================================")

if __name__ == "__main__":
    asyncio.run(main())
